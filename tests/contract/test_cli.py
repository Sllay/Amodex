from __future__ import annotations

import json
from pathlib import Path

from amodex.contracts.cli import main
from amodex.contracts.ids import new_uuid7
from amodex.contracts.models import SideEffectClass


def test_cli_validates_and_canonicalizes_tool_call(tmp_path: Path, capsys) -> None:
    tool_call_id, task_id, attempt_id = [new_uuid7() for _ in range(3)]
    payload = {
        "schema_version": 1,
        "tool_call_id": tool_call_id,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "tool_name": "git.update_ref",
        "arguments": {"ref": "refs/heads/test"},
        "side_effect_class": SideEffectClass.CONDITIONALLY_IDEMPOTENT.value,
        "idempotency_key": "test-1",
        "expected_revision": 4,
    }
    source = tmp_path / "tool-call.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    assert main(["validate", "tool-call", str(source)]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["tool_name"] == "git.update_ref"
    assert parsed["expected_revision"] == 4


def test_cli_returns_nonzero_for_invalid_payload(tmp_path: Path, capsys) -> None:
    source = tmp_path / "bad.json"
    source.write_text('{"schema_version":2}', encoding="utf-8")
    assert main(["validate", "tool-call", str(source)]) == 1
    assert capsys.readouterr().err
