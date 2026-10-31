#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import selectors
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


class ProbeFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeFailure(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--npx", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--godot", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--iterations", type=int, default=2)
    return parser.parse_args()


def parse_json_text(parts: list[str]) -> Any:
    for text in parts:
        stripped = text.strip()
        if not stripped:
            continue
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    joined = "\n".join(parts).strip()
    if joined:
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return {"text": joined}
    return {}


def recursive_find(value: Any, key: str) -> Any | None:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = recursive_find(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = recursive_find(child, key)
            if found is not None:
                return found
    return None


def recursive_collect_strings(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for child in value.values():
            out.extend(recursive_collect_strings(child))
    elif isinstance(value, list):
        for child in value:
            out.extend(recursive_collect_strings(child))
    return out


def png_dimensions(data: bytes) -> tuple[int, int]:
    require(data.startswith(b"\x89PNG\r\n\x1a\n"), "screenshot is not PNG")
    require(len(data) >= 24, "PNG too short")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def snapshot_godot_processes(godot_name: str) -> tuple[int, float]:
    try:
        proc = subprocess.run(
            ["ps", "-eo", "rss=,%cpu=,args="],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return 0, 0.0
    rss_total = 0
    cpu_total = 0.0
    for line in proc.stdout.splitlines():
        if godot_name not in line:
            continue
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            rss_total += int(parts[0])
            cpu_total += float(parts[1])
        except ValueError:
            continue
    return rss_total, cpu_total


def main() -> int:
    args = parse_args()
    project = str(Path(args.project).resolve())
    godot = str(Path(args.godot).resolve())
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    require(args.iterations >= 2, "P0-09 requires at least two repeated runs")
    require(bool(os.environ.get("DISPLAY")), "DISPLAY is required for P0-09")

    env = os.environ.copy()
    env.update({
        "GODOT_PATH": godot,
        "GODOT_MCP_LOG_LEVEL": "info",
        "GODOT_MCP_HEADLESS_TIMEOUT_MS": "60000",
        "GODOT_MCP_LSP_TIMEOUT_MS": "30000",
        "LIBGL_ALWAYS_SOFTWARE": "1",
    })

    cmd = [args.npx, "-y", args.package, "--project", project, "--mode", "full"]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None

    stderr_lines: list[str] = []

    def drain_stderr() -> None:
        for line in proc.stderr:
            stderr_lines.append(line.rstrip())

    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stderr_thread.start()

    resource_stop = threading.Event()
    resource_stats = {"peak_rss_kib": 0, "peak_cpu_percent_sum": 0.0}
    godot_name = Path(godot).name

    def sample_resources() -> None:
        while not resource_stop.wait(0.2):
            rss, cpu = snapshot_godot_processes(godot_name)
            resource_stats["peak_rss_kib"] = max(resource_stats["peak_rss_kib"], rss)
            resource_stats["peak_cpu_percent_sum"] = max(
                resource_stats["peak_cpu_percent_sum"], cpu
            )

    sampler_thread = threading.Thread(target=sample_resources, daemon=True)
    sampler_thread.start()

    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    next_id = 1

    def send(payload: dict[str, Any]) -> None:
        proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        proc.stdin.flush()

    def receive_for_id(message_id: int, timeout: float = 90.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            events = selector.select(timeout=min(1.0, remaining))
            if not events:
                if proc.poll() is not None:
                    raise ProbeFailure(
                        f"MCP server exited early rc={proc.returncode}; stderr={stderr_lines[-30:]}"
                    )
                continue
            line = proc.stdout.readline()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProbeFailure(f"non-JSON MCP stdout: {line!r}") from exc
            if message.get("id") == message_id:
                return message
        raise ProbeFailure(f"timed out waiting for MCP response id={message_id}")

    def request(method: str, params: dict[str, Any], timeout: float = 90.0) -> dict[str, Any]:
        nonlocal next_id
        message_id = next_id
        next_id += 1
        send({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params})
        response = receive_for_id(message_id, timeout=timeout)
        if "error" in response:
            raise ProbeFailure(f"MCP {method} failed: {response['error']}")
        return response.get("result", {})

    def call_tool(name: str, arguments: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
        result = request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout=timeout,
        )
        if result.get("isError") is True:
            raise ProbeFailure(f"{name} reported error: {result}")
        texts: list[str] = []
        images: list[bytes] = []
        image_mimes: list[str] = []
        for part in result.get("content", []):
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                texts.append(str(part.get("text", "")))
            elif part.get("type") == "image":
                data = part.get("data")
                if isinstance(data, str):
                    images.append(base64.b64decode(data))
                    image_mimes.append(str(part.get("mimeType", "")))
        return {
            "payload": parse_json_text(texts),
            "texts": texts,
            "images": images,
            "image_mimes": image_mimes,
            "raw": result,
        }

    def extract_run_id(response: dict[str, Any]) -> str:
        payload = response["payload"]
        found = recursive_find(payload, "run_id")
        if isinstance(found, str) and found:
            return found
        for text in response["texts"]:
            match = re.search(r'run[_ -]?id["\s:=]+([A-Za-z0-9_-]+)', text, re.I)
            if match:
                return match.group(1)
        raise ProbeFailure(f"could not find run_id in response: {response['payload']}")

    def tool_args_from_schema(tool: dict[str, Any], requested: dict[str, Any]) -> dict[str, Any]:
        schema = tool.get("inputSchema", {})
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        required = schema.get("required", []) if isinstance(schema, dict) else []
        result: dict[str, Any] = {}
        for key, value in requested.items():
            if key in properties or not properties:
                result[key] = value
        for key in required:
            if key in result:
                continue
            if key == "scene":
                result[key] = "res://main.tscn"
            elif key == "headless":
                result[key] = False
            elif key == "wait_seconds":
                result[key] = 10
            else:
                raise ProbeFailure(
                    f"unsupported required field for {tool.get('name')}: {key}; schema={schema}"
                )
        return result

    active_run: str | None = None
    iterations: list[dict[str, Any]] = []

    try:
        init = request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "amodex-p0-09", "version": "1"},
            },
        )
        protocol_version = init.get("protocolVersion")
        require(bool(protocol_version), f"missing MCP protocol version: {init}")
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        listed = request("tools/list", {})
        tools = listed.get("tools", [])
        tool_map = {
            tool.get("name"): tool for tool in tools if isinstance(tool, dict) and tool.get("name")
        }
        required_tools = {
            "godot_status",
            "godot_run_start",
            "godot_run_stop",
            "godot_run_output",
            "godot_game_tree",
            "godot_game_get",
            "godot_game_input",
            "godot_game_screenshot",
            "godot_game_perf",
        }
        missing = sorted(required_tools.difference(tool_map))
        require(not missing, f"required P0-09 tools missing: {missing}")

        status = call_tool("godot_status", {})
        require(
            recursive_find(status["payload"], "addon_installed") is True,
            f"Godot MCP addon not installed: {status['payload']}",
        )

        for iteration in range(1, args.iterations + 1):
            start_t = time.monotonic()
            start_args = tool_args_from_schema(
                tool_map["godot_run_start"],
                {"scene": "res://main.tscn", "headless": False, "wait_seconds": 10},
            )
            started = call_tool("godot_run_start", start_args, timeout=120.0)
            active_run = extract_run_id(started)

            tree: dict[str, Any] | None = None
            for _ in range(50):
                try:
                    candidate = call_tool(
                        "godot_game_tree",
                        {"run_id": active_run, "text": "CLICK_ME", "max_nodes": 30},
                        timeout=10.0,
                    )
                    nodes = recursive_find(candidate["payload"], "nodes")
                    if isinstance(nodes, list) and nodes:
                        tree = candidate
                        break
                except ProbeFailure:
                    pass
                time.sleep(0.2)
            require(tree is not None, "running game never exposed CLICK_ME through MCP")
            startup_ms = round((time.monotonic() - start_t) * 1000.0, 2)

            button_nodes = recursive_find(tree["payload"], "nodes")
            button = next(
                (node for node in button_nodes if isinstance(node, dict) and node.get("text") == "CLICK_ME"),
                None,
            )
            require(isinstance(button, dict), f"CLICK_ME button not found: {button_nodes}")
            button_path = button.get("path")
            require(isinstance(button_path, str) and button_path, f"button path missing: {button}")

            status_tree = call_tool(
                "godot_game_tree",
                {"run_id": active_run, "text": "WAITING_INPUT", "max_nodes": 30},
            )
            status_nodes = recursive_find(status_tree["payload"], "nodes")
            status_node = next(
                (
                    node
                    for node in status_nodes
                    if isinstance(node, dict) and node.get("text") == "WAITING_INPUT"
                ),
                None,
            )
            require(isinstance(status_node, dict), f"WAITING_INPUT label not found: {status_nodes}")
            status_path = status_node.get("path")
            require(isinstance(status_path, str) and status_path, "status path missing")

            before_t = time.monotonic()
            before = call_tool(
                "godot_game_screenshot",
                {
                    "run_id": active_run,
                    "source": "game",
                    "max_size": 640,
                    "format": "png",
                    "save_to": f"user://p009-before-{iteration}.png",
                },
                timeout=60.0,
            )
            before_ms = round((time.monotonic() - before_t) * 1000.0, 2)
            require(len(before["images"]) == 1, "before screenshot did not return one image block")
            before_bytes = before["images"][0]
            before_dims = png_dimensions(before_bytes)
            require(before_dims == (640, 360), f"before screenshot size unexpected: {before_dims}")
            before_path = output_dir / f"before-{iteration}.png"
            before_path.write_bytes(before_bytes)

            input_response = call_tool(
                "godot_game_input",
                {
                    "run_id": active_run,
                    "steps": [
                        {
                            "type": "click",
                            "at": button_path,
                            "button": "left",
                            "phase": "tap",
                            "ms": 80,
                        },
                        {"type": "wait", "frames": 3},
                    ],
                },
                timeout=30.0,
            )

            changed_tree: dict[str, Any] | None = None
            for _ in range(30):
                candidate = call_tool(
                    "godot_game_tree",
                    {"run_id": active_run, "text": "INPUT_OK", "max_nodes": 30},
                )
                nodes = recursive_find(candidate["payload"], "nodes")
                if isinstance(nodes, list) and nodes:
                    changed_tree = candidate
                    break
                time.sleep(0.1)
            require(changed_tree is not None, "native MCP input did not produce INPUT_OK")

            get_status = call_tool(
                "godot_game_get",
                {"run_id": active_run, "path": status_path, "properties": ["text"]},
            )
            status_text = recursive_find(get_status["payload"], "text")
            if status_text != "INPUT_OK":
                properties = recursive_find(get_status["payload"], "properties")
                if isinstance(properties, dict):
                    status_text = properties.get("text")
            require(status_text == "INPUT_OK", f"status label did not change: {get_status['payload']}")

            after_t = time.monotonic()
            after = call_tool(
                "godot_game_screenshot",
                {
                    "run_id": active_run,
                    "source": "game",
                    "max_size": 640,
                    "format": "png",
                    "save_to": f"user://p009-after-{iteration}.png",
                    "compare_to": f"user://p009-before-{iteration}.png",
                    "tolerance": 8,
                },
                timeout=60.0,
            )
            after_ms = round((time.monotonic() - after_t) * 1000.0, 2)
            require(len(after["images"]) == 1, "after screenshot did not return one image block")
            after_bytes = after["images"][0]
            after_dims = png_dimensions(after_bytes)
            require(after_dims == (640, 360), f"after screenshot size unexpected: {after_dims}")
            after_path = output_dir / f"after-{iteration}.png"
            after_path.write_bytes(after_bytes)

            before_sha = hashlib.sha256(before_bytes).hexdigest()
            after_sha = hashlib.sha256(after_bytes).hexdigest()
            require(before_sha != after_sha, "before/after screenshot hashes are identical")
            changed_percent = recursive_find(after["payload"], "changed_percent")
            if changed_percent is not None:
                require(float(changed_percent) > 0.0, f"screenshot diff did not change: {after['payload']}")

            perf = call_tool(
                "godot_game_perf",
                {"run_id": active_run, "sample_seconds": 0.5, "monitors": True},
                timeout=30.0,
            )
            display = recursive_find(perf["payload"], "display")
            rendering_method = recursive_find(perf["payload"], "rendering_method")
            require(
                isinstance(display, str) and display.lower() != "headless",
                f"visual run is not windowed: display={display}; perf={perf['payload']}",
            )

            output = call_tool("godot_run_output", {"run_id": active_run}, timeout=20.0)
            output_text = "\n".join(recursive_collect_strings(output["payload"]))
            require(
                "AMODEX_P0_09_WINDOWED_READY" in output_text,
                f"windowed ready marker missing: {output['payload']}",
            )
            require(
                "AMODEX_P0_09_INPUT_OK" in output_text,
                f"input marker missing: {output['payload']}",
            )
            require(
                "SCRIPT ERROR" not in output_text and "Parse Error" not in output_text,
                f"Godot runtime reported script/parser errors: {output_text[-4000:]}",
            )

            stop_response = call_tool("godot_run_stop", {"run_id": active_run}, timeout=30.0)
            active_run = None

            iterations.append(
                {
                    "iteration": iteration,
                    "run_id": recursive_find(stop_response["payload"], "run_id") or "stopped",
                    "startup_ms": startup_ms,
                    "before_screenshot_ms": before_ms,
                    "after_screenshot_ms": after_ms,
                    "before_sha256": before_sha,
                    "after_sha256": after_sha,
                    "image_size": list(before_dims),
                    "button_path": button_path,
                    "status_path": status_path,
                    "input_result": input_response["payload"],
                    "diff_changed_percent": changed_percent,
                    "display": display,
                    "rendering_method": rendering_method,
                    "perf": perf["payload"],
                }
            )

        require(
            len({entry["before_sha256"] for entry in iterations}) >= 1,
            "no repeated-run screenshot evidence recorded",
        )
        evidence = {
            "status": "PASS",
            "protocol_version": protocol_version,
            "display_env": os.environ.get("DISPLAY"),
            "iterations": iterations,
            "repeated_runs": len(iterations),
            "peak_godot_rss_mib": round(resource_stats["peak_rss_kib"] / 1024.0, 2),
            "peak_godot_cpu_percent_sum": round(resource_stats["peak_cpu_percent_sum"], 2),
            "stderr_tail": stderr_lines[-30:],
        }
        (output_dir / "evidence.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(evidence, indent=2, sort_keys=True))
        return 0
    finally:
        if active_run:
            try:
                call_tool("godot_run_stop", {"run_id": active_run}, timeout=10.0)
            except Exception:
                pass
        resource_stop.set()
        sampler_thread.join(timeout=2)
        try:
            proc.stdin.close()
        except OSError:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
