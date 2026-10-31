#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import selectors
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--npx", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--godot", required=True)
    parser.add_argument("--require-addon", action="store_true")
    parser.add_argument("--require-scene-backend", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project = str(Path(args.project).resolve())
    godot = str(Path(args.godot).resolve())

    env = os.environ.copy()
    env.update({
        "GODOT_PATH": godot,
        "GODOT_MCP_LOG_LEVEL": "info",
        "GODOT_MCP_HEADLESS_TIMEOUT_MS": "60000",
        "GODOT_MCP_LSP_TIMEOUT_MS": "30000",
    })

    cmd = [
        args.npx,
        "-y",
        args.package,
        "--project",
        project,
        "--mode",
        "compact",
        "--read-only",
    ]

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

    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)

    def send(payload: dict[str, Any]) -> None:
        proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        proc.stdin.flush()

    def receive_for_id(message_id: int, timeout: float = 60.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            events = selector.select(timeout=min(1.0, remaining))
            if not events:
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"MCP server exited early rc={proc.returncode}; stderr={stderr_lines[-20:]}"
                    )
                continue
            line = proc.stdout.readline()
            if line == "":
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"non-JSON stdout from MCP server: {line!r}") from exc
            if message.get("id") == message_id:
                return message
        raise TimeoutError(f"timed out waiting for MCP response id={message_id}")

    try:
        send({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "amodex-p0-08", "version": "1"},
            },
        })
        init = receive_for_id(1)
        if "error" in init:
            raise RuntimeError(f"MCP initialize failed: {init['error']}")
        protocol_version = init.get("result", {}).get("protocolVersion")
        if not protocol_version:
            raise RuntimeError(f"MCP initialize missing protocol version: {init}")

        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = receive_for_id(2)
        if "error" in listed:
            raise RuntimeError(f"MCP tools/list failed: {listed['error']}")
        tools = listed.get("result", {}).get("tools", [])
        names = [tool.get("name") for tool in tools if isinstance(tool, dict)]
        if "godot_status" not in names:
            raise RuntimeError(f"godot_status missing from MCP tool surface: {names}")

        status_tool = next(tool for tool in tools if tool.get("name") == "godot_status")
        required = status_tool.get("inputSchema", {}).get("required", [])
        if required:
            raise RuntimeError(f"godot_status unexpectedly requires arguments: {required}")

        send({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "godot_status", "arguments": {}},
        })
        status = receive_for_id(3, timeout=90.0)
        if "error" in status:
            raise RuntimeError(f"godot_status JSON-RPC error: {status['error']}")
        status_result = status.get("result", {})
        if status_result.get("isError") is True:
            raise RuntimeError(f"godot_status tool reported error: {status_result}")

        content = status_result.get("content", [])
        text_parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        status_text = "\n".join(text_parts)
        if not status_text.strip():
            raise RuntimeError("godot_status returned no textual status")

        try:
            status_payload = json.loads(status_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"godot_status text was not JSON: {status_text[:800]!r}") from exc

        addon_installed = status_payload.get("addon_installed") is True
        scene_backend = status_payload.get("scene_backend")
        if args.require_addon and not addon_installed:
            raise RuntimeError(f"Godot MCP addon was required but not installed: {status_payload}")
        if args.require_scene_backend and scene_backend in {None, "unavailable"}:
            raise RuntimeError(f"Godot MCP scene backend unavailable after addon install: {status_payload}")

        print(json.dumps({
            "status": "PASS",
            "protocol_version": protocol_version,
            "tool_count": len(names),
            "godot_status_present": True,
            "godot_status_call_succeeded": True,
            "read_only": True,
            "project": Path(project).name,
            "addon_installed": addon_installed,
            "scene_backend": scene_backend,
            "editor_connected": status_payload.get("editor_connected"),
            "godot_version": status_payload.get("godot", {}).get("version"),
            "server_version": status_payload.get("server_version"),
            "status_payload": status_payload,
            "stderr_tail": stderr_lines[-10:],
        }, indent=2, sort_keys=True))
        return 0
    finally:
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
