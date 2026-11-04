#!/usr/bin/env python3
"""Seed/verify durable workspace state across Control Plane restarts.

This probe is intended for isolated CI/activation environments with the private
access gate disabled. It writes a uniquely named project/chat/message during
`seed`, stores only their public IDs/content in a local JSON state file, then
`verify` checks the same data after the process has been restarted.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _json_request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = None
    headers = {"Accept": "application/json", "User-Agent": "amodex-persistence-probe/1"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(base_url.rstrip("/") + path, data=data, method=method, headers=headers)
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read()
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            return int(response.status), payload
    except HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            payload = {}
        return int(exc.code), payload


def seed(base_url: str, state_file: Path) -> None:
    marker = uuid.uuid4().hex[:12]
    project_name = f"activation-probe-{marker}"
    chat_title = f"restart-{marker}"
    message = f"persist-after-restart-{marker}"

    status, project = _json_request(
        base_url,
        "/v1/ui/projects",
        method="POST",
        body={"name": project_name, "description": "P0 activation restart persistence probe"},
    )
    if status != 201:
        raise RuntimeError(f"project seed failed: HTTP {status}")
    workspace_id = str(project["workspace_id"])

    status, chat = _json_request(
        base_url,
        f"/v1/ui/projects/{workspace_id}/chats",
        method="POST",
        body={"title": chat_title, "active_environment_id": None},
    )
    if status != 201:
        raise RuntimeError(f"chat seed failed: HTTP {status}")
    chat_id = str(chat["chat_id"])

    status, owner = _json_request(
        base_url,
        f"/v1/ui/chats/{chat_id}/messages",
        method="POST",
        body={"role": "OWNER", "content": message},
    )
    if status != 201:
        raise RuntimeError(f"message seed failed: HTTP {status}")

    state = {
        "workspace_id": workspace_id,
        "chat_id": chat_id,
        "message_id": str(owner["message_id"]),
        "project_name": project_name,
        "chat_title": chat_title,
        "message": message,
    }
    state_file.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    print("ACTIVATION_PERSISTENCE_SEEDED " + json.dumps(state, sort_keys=True))


def verify(base_url: str, state_file: Path) -> None:
    state = json.loads(state_file.read_text(encoding="utf-8"))
    status, snapshot = _json_request(base_url, f"/v1/ui/chats/{state['chat_id']}")
    if status != 200:
        raise RuntimeError(f"chat verification failed: HTTP {status}")

    if str(snapshot.get("workspace", {}).get("workspace_id")) != str(state["workspace_id"]):
        raise RuntimeError("workspace identity changed across restart")
    if str(snapshot.get("chat", {}).get("chat_id")) != str(state["chat_id"]):
        raise RuntimeError("chat identity changed across restart")

    messages = snapshot.get("messages") or []
    matches = [
        item
        for item in messages
        if str(item.get("message_id")) == str(state["message_id"])
        and item.get("content") == state["message"]
        and item.get("role") == "OWNER"
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one persisted owner message, found {len(matches)}")

    print(
        "ACTIVATION_PERSISTENCE_OK "
        + json.dumps(
            {
                "workspace_id": state["workspace_id"],
                "chat_id": state["chat_id"],
                "message_id": state["message_id"],
            },
            sort_keys=True,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Amodex restart persistence activation probe")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("seed", "verify"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--url", required=True)
        sub.add_argument("--state-file", required=True)
    args = parser.parse_args()

    try:
        if args.command == "seed":
            seed(args.url, Path(args.state_file))
        else:
            verify(args.url, Path(args.state_file))
    except (RuntimeError, OSError, KeyError, ValueError, URLError) as exc:
        print(f"ACTIVATION_PERSISTENCE_FAILED {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
