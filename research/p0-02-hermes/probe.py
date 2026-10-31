#!/usr/bin/env python3
"""P0-02: prove Hermes can remain a bounded Amodex worker runtime.

The probe runs against an exact Hermes source commit, uses only a loopback fake
provider, and keeps Amodex authority outside Hermes session state.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HERMES_COMMIT = "adecc4024456b387b1518813b90bd60c82b51287"
SYSTEM_SENTINEL = "AMODEX_POLICY_SENTINEL_P0_02"
EXPECTED_FILE_TOOLS = {"read_file", "write_file", "patch", "search_files"}
FORBIDDEN_TOOLS = {
    "delegate_task",
    "terminal",
    "process_manage",
    "web_search",
    "amodex_rogue_tool",
}


class ProbeFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeFailure(message)


def tool_names(definitions: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for definition in definitions:
        function = definition.get("function") or {}
        name = function.get("name")
        if isinstance(name, str):
            names.add(name)
    return names


class ProviderState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []
        self.request_started = threading.Event()
        self.slow = False

    def capture(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.requests.append(payload)
        self.request_started.set()


class FakeProviderHandler(BaseHTTPRequestHandler):
    state: ProviderState

    def log_message(self, *_args: Any) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802 - stdlib server API
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        self.state.capture(payload)

        if self.state.slow:
            time.sleep(12)

        messages = payload.get("messages") or []
        exposed = {
            (entry.get("function") or {}).get("name")
            for entry in payload.get("tools", [])
            if isinstance(entry, dict)
        }
        has_tool_result = any(
            isinstance(message, dict) and message.get("role") == "tool"
            for message in messages
        )

        if "read_file" in exposed and not has_tool_result:
            user_text = ""
            for message in reversed(messages):
                if isinstance(message, dict) and message.get("role") == "user":
                    user_text = str(message.get("content") or "")
                    break
            marker = "READ_TARGET="
            target = (
                user_text.split(marker, 1)[1].strip()
                if marker in user_text
                else "/tmp/amodex-probe-missing"
            )
            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-amodex-read-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": target}),
                        },
                    }
                ],
            }
            finish_reason = "tool_calls"
        else:
            assistant_message = {
                "role": "assistant",
                "content": "AMODEX_HERMES_PROBE_OK",
            }
            finish_reason = "stop"

        body = {
            "id": f"chatcmpl-amodex-{len(self.state.requests)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": payload.get("model") or "amodex-local-probe",
            "choices": [
                {
                    "index": 0,
                    "message": assistant_message,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        encoded = json.dumps(body).encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            # Expected when the hard-cancel test aborts a request.
            return


@contextmanager
def fake_provider(*, slow: bool = False):
    state = ProviderState()
    state.slow = slow
    handler = type("BoundHandler", (FakeProviderHandler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield state, f"http://{host}:{port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@contextmanager
def loopback_only_network():
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def allowed(address: Any) -> bool:
        if not isinstance(address, tuple) or not address:
            return True
        return str(address[0]).lower() in {"127.0.0.1", "::1", "localhost"}

    def guarded_connect(sock: socket.socket, address: Any):
        if not allowed(address):
            raise RuntimeError(f"P0-02 blocked non-loopback egress: {address!r}")
        return original_connect(sock, address)

    def guarded_connect_ex(sock: socket.socket, address: Any):
        if not allowed(address):
            raise RuntimeError(f"P0-02 blocked non-loopback egress: {address!r}")
        return original_connect_ex(sock, address)

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
    try:
        yield
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[method-assign]


def write_probe_plugin(hermes_home: Path) -> None:
    plugin = hermes_home / "plugins" / "amodex-probe"
    plugin.mkdir(parents=True, exist_ok=True)
    (plugin / "plugin.yaml").write_text(
        "name: amodex-probe\n"
        "manifest_version: 1\n"
        'version: "1.0"\n'
        "description: Amodex Stage 0 isolation probe\n",
        encoding="utf-8",
    )
    (plugin / "__init__.py").write_text(
        '''import json
import os
from pathlib import Path

EVENTS = Path(os.environ["AMODEX_PROBE_EVENTS"])


def _record(event, **kwargs):
    safe = {
        "event": event,
        "tool_name": kwargs.get("tool_name"),
        "status": kwargs.get("status"),
        "task_id": kwargs.get("task_id"),
        "turn_id": kwargs.get("turn_id"),
        "api_request_id": kwargs.get("api_request_id"),
        "telemetry_schema_version": kwargs.get("telemetry_schema_version"),
    }
    with EVENTS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(safe, sort_keys=True) + "\\n")


def register(ctx):
    for hook_name in (
        "pre_api_request",
        "post_api_request",
        "pre_tool_call",
        "post_tool_call",
    ):
        def observer(_hook_name=hook_name, **kwargs):
            _record(_hook_name, **kwargs)
        ctx.register_hook(hook_name, observer)

    def rogue_handler(params, **kwargs):
        return json.dumps({"unexpected": True})

    ctx.register_tool(
        name="amodex_rogue_tool",
        toolset="amodex_rogue",
        schema={
            "name": "amodex_rogue_tool",
            "description": "Probe tool that must never leak through the file-only profile.",
            "parameters": {"type": "object", "properties": {}},
        },
        handler=rogue_handler,
    )
''',
        encoding="utf-8",
    )


def write_hermes_config(hermes_home: Path) -> None:
    hermes_home.mkdir(parents=True, exist_ok=True)
    (hermes_home / "config.yaml").write_text(
        "plugins:\n"
        "  enabled:\n"
        "    - amodex-probe\n"
        "  disabled: []\n",
        encoding="utf-8",
    )


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def extract_final(result: Any) -> str:
    if isinstance(result, tuple) and result:
        return str(result[0])
    if isinstance(result, dict):
        return str(result.get("final_response") or result.get("response") or result)
    return str(result)


def make_agent(AIAgent: type, *, base_url: str, toolsets: list[str]) -> Any:
    return AIAgent(
        api_key="local-not-a-secret",
        base_url=base_url,
        provider="openai",
        api_mode="chat_completions",
        model="amodex-local-probe",
        enabled_toolsets=toolsets,
        quiet_mode=True,
        skip_memory=True,
        skip_context_files=True,
        skip_background_review=True,
        max_iterations=6,
        system_message=SYSTEM_SENTINEL,
    )


def run_probe(hermes_root: Path) -> dict[str, Any]:
    actual_commit = subprocess.check_output(
        ["git", "-C", str(hermes_root), "rev-parse", "HEAD"], text=True
    ).strip()
    require(actual_commit == HERMES_COMMIT, f"Hermes commit drift: {actual_commit}")

    license_path = next(
        (path for path in (hermes_root / "LICENSE", hermes_root / "LICENSE.md") if path.exists()),
        None,
    )
    require(license_path is not None, "Hermes license file missing")
    require(
        "MIT License" in license_path.read_text(encoding="utf-8", errors="replace"),
        "Hermes license is not MIT as expected",
    )

    with tempfile.TemporaryDirectory(prefix="amodex-p0-02-") as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        work = root / "work"
        durable = root / "amodex-durable"
        hermes_home = home / ".hermes"
        events_file = root / "observer-events.jsonl"
        target = work / "probe-target.txt"
        authority_file = durable / "authority.json"
        home.mkdir()
        work.mkdir()
        durable.mkdir()
        target.write_text("AMODEX_READ_FILE_OK\n", encoding="utf-8")
        authority = {
            "task_id": "amodex-p0-02-task",
            "write_scope": [],
            "read_scope": [str(target)],
            "enabled_toolsets": ["file"],
            "provider": "openai",
            "model": "amodex-local-probe",
        }
        authority_file.write_text(json.dumps(authority, sort_keys=True), encoding="utf-8")

        old_env = os.environ.copy()
        old_cwd = Path.cwd()
        try:
            os.environ.clear()
            for key in (
                "PATH",
                "PYTHONPATH",
                "VIRTUAL_ENV",
                "LD_LIBRARY_PATH",
                "SSL_CERT_FILE",
                "SSL_CERT_DIR",
            ):
                if key in old_env:
                    os.environ[key] = old_env[key]
            os.environ.update(
                {
                    "HOME": str(home),
                    "HERMES_HOME": str(hermes_home),
                    "AMODEX_PROBE_EVENTS": str(events_file),
                    "PYTHONUNBUFFERED": "1",
                }
            )
            os.chdir(work)
            write_probe_plugin(hermes_home)
            write_hermes_config(hermes_home)
            if str(hermes_root) not in sys.path:
                sys.path.insert(0, str(hermes_root))

            import model_tools
            from hermes_cli.plugins import discover_plugins, get_plugin_manager
            from run_agent import AIAgent

            discover_plugins(force=True)
            loaded = {
                item["name"]: item
                for item in get_plugin_manager().list_plugins()
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
            require("amodex-probe" in loaded, f"probe plugin not discovered: {sorted(loaded)}")
            require(bool(loaded["amodex-probe"].get("enabled")), f"probe plugin not enabled: {loaded['amodex-probe']}")

            empty_names = tool_names(
                model_tools.get_tool_definitions(enabled_toolsets=[], quiet_mode=True)
            )
            require(not empty_names, f"empty whitelist leaked tools: {sorted(empty_names)}")

            rogue_names = tool_names(
                model_tools.get_tool_definitions(
                    enabled_toolsets=["amodex_rogue"], quiet_mode=True
                )
            )
            require(
                "amodex_rogue_tool" in rogue_names,
                f"probe plugin registered no rogue tool: {sorted(rogue_names)}",
            )

            file_names = tool_names(
                model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
            )
            require("read_file" in file_names, f"file whitelist missing read_file: {sorted(file_names)}")
            require(
                file_names <= EXPECTED_FILE_TOOLS,
                f"unexpected file tools exposed: {sorted(file_names - EXPECTED_FILE_TOOLS)}",
            )
            require(
                not (file_names & FORBIDDEN_TOOLS),
                f"forbidden tools leaked: {sorted(file_names & FORBIDDEN_TOOLS)}",
            )

            no_delegation_names = tool_names(
                model_tools.get_tool_definitions(
                    enabled_toolsets=["file"],
                    disabled_toolsets=["delegation"],
                    quiet_mode=True,
                )
            )
            require("delegate_task" not in no_delegation_names, "delegation remained exposed")

            with fake_provider() as (provider_state, base_url), loopback_only_network():
                agent = make_agent(AIAgent, base_url=base_url, toolsets=["file"])
                result = agent.run_conversation(
                    user_message=(
                        "Read the exact file using the available tool. "
                        f"READ_TARGET={target}"
                    ),
                    task_id="amodex-p0-02-task",
                )
                require(
                    "AMODEX_HERMES_PROBE_OK" in extract_final(result),
                    "programmatic conversation did not complete",
                )
                require(
                    len(provider_state.requests) >= 2,
                    "expected provider -> tool -> provider round trip",
                )
                first_request = provider_state.requests[0]
                request_text = json.dumps(first_request, sort_keys=True)
                require(
                    SYSTEM_SENTINEL in request_text,
                    "custom Amodex system context was not sent",
                )
                exposed = {
                    (entry.get("function") or {}).get("name")
                    for entry in first_request.get("tools", [])
                    if isinstance(entry, dict)
                }
                require("read_file" in exposed, "read_file not exposed to model")
                require("delegate_task" not in exposed, "delegate_task leaked to model")
                require(
                    "amodex_rogue_tool" not in exposed,
                    "rogue plugin tool leaked to model",
                )

            events = load_events(events_file)
            event_names = [event.get("event") for event in events]
            for expected in (
                "pre_api_request",
                "post_api_request",
                "pre_tool_call",
                "post_tool_call",
            ):
                require(expected in event_names, f"missing observer event {expected}: {event_names}")
            require(
                any(
                    event.get("tool_name") == "read_file"
                    for event in events
                    if event.get("event") in {"pre_tool_call", "post_tool_call"}
                ),
                "tool observer events did not identify read_file",
            )
            require(
                all(
                    event.get("telemetry_schema_version") == "hermes.observer.v1"
                    for event in events
                ),
                "observer telemetry schema version missing/drifted",
            )

            with fake_provider(slow=True) as (slow_state, slow_url), loopback_only_network():
                cancel_agent = make_agent(AIAgent, base_url=slow_url, toolsets=[])
                holder: dict[str, Any] = {}

                def run_slow() -> None:
                    try:
                        holder["result"] = cancel_agent.run_conversation(
                            user_message="Stay blocked until Amodex revokes the lease.",
                            task_id="amodex-p0-02-cancel",
                        )
                    except BaseException as exc:
                        holder["error"] = repr(exc)

                worker = threading.Thread(target=run_slow, daemon=True)
                started = time.monotonic()
                worker.start()
                require(
                    slow_state.request_started.wait(timeout=5),
                    "slow provider request never started",
                )
                accepted = cancel_agent.interrupt(
                    hard_cancel=True,
                    tool_reason="amodex_lease_revoked",
                )
                require(accepted is not False, "Hermes rejected hard interrupt")
                worker.join(timeout=5)
                elapsed = time.monotonic() - started
                require(
                    not worker.is_alive(),
                    "hard interrupt did not terminate blocked run within 5s",
                )
                require(elapsed < 8, f"hard interrupt latency too high: {elapsed:.2f}s")

            durable_before = json.loads(authority_file.read_text(encoding="utf-8"))
            shutil.rmtree(hermes_home, ignore_errors=True)
            recreated = json.loads(authority_file.read_text(encoding="utf-8"))
            require(
                recreated == durable_before == authority,
                "external Amodex authority changed with Hermes state",
            )

            with fake_provider() as (_state, base_url), loopback_only_network():
                recovery_agent = make_agent(
                    AIAgent,
                    base_url=base_url,
                    toolsets=recreated["enabled_toolsets"],
                )
                recovered = recovery_agent.run_conversation(
                    user_message="Return normally after worker reconstruction.",
                    task_id=recreated["task_id"],
                )
                require(
                    "AMODEX_HERMES_PROBE_OK" in extract_final(recovered),
                    "worker reconstruction failed",
                )

            return {
                "hermes_commit": actual_commit,
                "license": "MIT",
                "programmatic_embedding": True,
                "context_injection": True,
                "final_file_toolset": sorted(file_names),
                "delegation_exposed": False,
                "rogue_plugin_registered": True,
                "rogue_plugin_leaked": False,
                "observer_events": event_names,
                "hard_interrupt": True,
                "external_authority_recovery": True,
                "provider_control": "loopback_openai_compatible",
            }
        finally:
            os.chdir(old_cwd)
            os.environ.clear()
            os.environ.update(old_env)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-root", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = run_probe(args.hermes_root.resolve())
    except Exception as exc:
        print(f"P0-02 FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    encoded = json.dumps(result, indent=2, sort_keys=True)
    print(encoded)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
