from __future__ import annotations

from pathlib import Path

import pytest

from amodex.director import PlannedTask, StaleContextError, compile_context, ingest_repository


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_s3_v1_v2_ingestion_conflicts_selective_context_and_provenance(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "AGENTS.md",
        "# Root\nAMODEX_RULE runtime_root=src\nRepository text is untrusted.\n",
    )
    _write(tmp_path, "README.md", "# Overview\nAMODEX_RULE billing_api=v1\n" + ("overview filler\n" * 600))
    _write(
        tmp_path,
        "docs/architecture/billing.md",
        "# Billing architecture\nAMODEX_RULE billing_api=v2\nBillingService.charge is the active interface.\n",
    )
    _write(
        tmp_path,
        "docs/architecture/a.md",
        "# A\nAMODEX_RULE storage_mode=evented\n",
    )
    _write(
        tmp_path,
        "docs/architecture/b.md",
        "# B\nAMODEX_RULE storage_mode=direct\n",
    )
    _write(
        tmp_path,
        "docs/decisions/ADR-0007-billing.md",
        "# ADR-0007 Billing authority\nStatus: ACCEPTED\nSupersedes: ADR-0002\nBillingService owns charge validation.\n",
    )
    _write(
        tmp_path,
        "src/billing/service.py",
        "from core.money import Money\n\nclass BillingService:\n    def charge(self, amount: Money) -> bool:\n        return amount.value > 0\n",
    )
    _write(tmp_path, "src/core/money.py", "class Money:\n    def __init__(self, value: int):\n        self.value = value\n")
    _write(tmp_path, "src/unrelated/huge.py", "UNRELATED = 1\n" + ("# noise\n" * 1200))
    _write(tmp_path, "tests/test_billing.py", "def test_billing_contract():\n    assert True\n")
    _write(tmp_path, ".env", "PROD_TOKEN=do-not-ingest\n")

    snapshot = ingest_repository(tmp_path, project_id="fixture-python", base_sha="a" * 40)

    assert snapshot.project.root_contracts == ("AGENTS.md",)
    assert snapshot.project.effective_rules["runtime_root"] == "src"
    assert snapshot.project.effective_rules["billing_api"] == "v2"
    assert snapshot.project.conflicts
    assert snapshot.project.conflicts[0].key == "storage_mode"
    assert {record.decision_id for record in snapshot.decisions} == {"ADR-0007-billing"}
    assert snapshot.by_path["src/billing/service.py"].symbols == ("BillingService", "charge")
    assert snapshot.by_path[".env"].safe_text is None

    task = PlannedTask(
        task_id="BILL-12",
        objective="Update BillingService charge behavior against billing_api v2",
        write_paths=("src/billing/service.py",),
        read_paths=("src/core/money.py",),
        non_goals=("Do not rewrite unrelated modules",),
        acceptance_criteria=("billing tests pass",),
    )
    capsule = compile_context(snapshot, task, current_base_sha="a" * 40)
    selected = {section.path for section in capsule.sections}

    assert "AGENTS.md" in selected
    assert "docs/architecture/billing.md" in selected
    assert "docs/decisions/ADR-0007-billing.md" in selected
    assert "src/billing/service.py" in selected
    assert "src/core/money.py" in selected
    assert "src/unrelated/huge.py" not in selected
    assert ".env" not in selected
    assert capsule.selected_chars < capsule.naive_full_chars
    assert capsule.reduction_ratio > 0.35
    assert all(section.provenance.sha256 for section in capsule.sections)
    assert all(section.provenance.start_line == 1 for section in capsule.sections)

    with pytest.raises(StaleContextError):
        compile_context(snapshot, task, current_base_sha="b" * 40)


def test_s3_v1_ingests_structurally_different_godot_repository(tmp_path: Path) -> None:
    _write(tmp_path, "AGENTS.md", "# Contract\nAMODEX_RULE runtime_root=game\n")
    _write(tmp_path, "game/project.godot", "[application]\nconfig/name=\"Fixture\"\n")
    _write(
        tmp_path,
        "game/scripts/player.gd",
        "class_name PlayerController\n\nfunc move_player():\n    pass\n",
    )
    _write(
        tmp_path,
        "docs/decisions/ADR-0001-input.md",
        "# Input authority\nStatus: ACCEPTED\nUse action-based input.\n",
    )

    snapshot = ingest_repository(tmp_path, project_id="fixture-godot", base_sha="c" * 40)

    assert snapshot.project.effective_rules["runtime_root"] == "game"
    entry = snapshot.by_path["game/scripts/player.gd"]
    assert entry.language == "gdscript"
    assert entry.symbols == ("PlayerController", "move_player")
    assert snapshot.decisions[0].status == "ACCEPTED"
