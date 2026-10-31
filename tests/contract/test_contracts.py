from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from amodex.contracts import (
    Authority,
    Checkpoint,
    CheckpointClass,
    EvidenceResult,
    Objective,
    RepositoryAuthority,
    Scope,
    SideEffectClass,
    TaskPackage,
    ToolCall,
    ValidationEvidence,
    WorkerLease,
    canonical_json,
    new_uuid7,
    validate_uuid7,
)

NOW = datetime(2026, 10, 31, 6, 0, tzinfo=timezone.utc)
SHA = "a" * 40


def ids(count: int) -> list[str]:
    return [new_uuid7(unix_ms=1_799_000_000_000 + index) for index in range(count)]


def task_package() -> TaskPackage:
    package_id, task_id, attempt_id = ids(3)
    return TaskPackage(
        package_id=package_id,
        task_id=task_id,
        attempt_id=attempt_id,
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        objective=Objective(statement="Prove executable contract semantics"),
        repository=RepositoryAuthority(
            repository_id="1440484419",
            expected_base_sha=SHA,
            task_branch="work/0/contracts",
        ),
        scope=Scope(
            write=("src/amodex/contracts/**",),
            read=("docs/contracts/RUNTIME_CONTRACTS.md",),
            protected=("LICENSE.md",),
            allowed_operations=("read", "patch"),
        ),
        required_capabilities=("filesystem.patch",),
        authority=Authority(may_promote=False),
    )


def test_uuid7_generator_and_validator() -> None:
    value = new_uuid7(unix_ms=1_799_000_000_000)
    assert validate_uuid7(value) == value
    assert value[14] == "7"


def test_task_package_round_trip_is_deterministic() -> None:
    original = task_package()
    encoded = canonical_json(original)
    decoded = TaskPackage.model_validate_json(encoded)
    assert decoded == original
    assert canonical_json(decoded) == encoded
    assert json.loads(encoded)["schema_version"] == 1


def test_unknown_authority_field_is_rejected() -> None:
    payload = task_package().model_dump(mode="json")
    payload["authority"]["worker_is_admin"] = True
    with pytest.raises(ValidationError, match="extra_forbidden"):
        TaskPackage.model_validate(payload)


def test_future_schema_version_is_rejected() -> None:
    payload = task_package().model_dump(mode="json")
    payload["schema_version"] = 2
    with pytest.raises(ValidationError):
        TaskPackage.model_validate(payload)


def test_snapshot_is_frozen_and_identity_cannot_evolve() -> None:
    package = task_package()
    with pytest.raises(ValidationError, match="frozen_instance"):
        package.package_revision = 2  # type: ignore[misc]

    with pytest.raises(ValueError, match="identity field"):
        package.evolve(task_id=new_uuid7())

    evolved = package.evolve(package_revision=2)
    assert evolved.package_revision == 2
    assert package.package_revision == 1


def test_invalid_enum_is_rejected() -> None:
    tool_call_id, task_id, attempt_id = ids(3)
    with pytest.raises(ValidationError):
        ToolCall(
            tool_call_id=tool_call_id,
            task_id=task_id,
            attempt_id=attempt_id,
            tool_name="filesystem.patch",
            side_effect_class="magic",  # type: ignore[arg-type]
        )


def test_write_tool_call_requires_idempotency_key() -> None:
    tool_call_id, task_id, attempt_id = ids(3)
    with pytest.raises(ValidationError, match="idempotency_key"):
        ToolCall(
            tool_call_id=tool_call_id,
            task_id=task_id,
            attempt_id=attempt_id,
            tool_name="git.push",
            side_effect_class=SideEffectClass.IDEMPOTENT_WRITE,
        )


def test_conditional_write_requires_expected_revision() -> None:
    tool_call_id, task_id, attempt_id = ids(3)
    with pytest.raises(ValidationError, match="expected_revision"):
        ToolCall(
            tool_call_id=tool_call_id,
            task_id=task_id,
            attempt_id=attempt_id,
            tool_name="git.update_ref",
            side_effect_class=SideEffectClass.CONDITIONALLY_IDEMPOTENT,
            idempotency_key="PROM-123",
        )


def test_validated_checkpoint_requires_real_evidence_reference() -> None:
    checkpoint_id, task_id, attempt_id = ids(3)
    with pytest.raises(ValidationError, match="evidence_id"):
        Checkpoint(
            checkpoint_id=checkpoint_id,
            task_id=task_id,
            attempt_id=attempt_id,
            checkpoint_class=CheckpointClass.VALIDATED,
            commit_sha=SHA,
            created_at=NOW,
        )


def test_evidence_binds_exact_sha_and_normalizes_utc() -> None:
    evidence_id, task_id = ids(2)
    evidence = ValidationEvidence(
        evidence_id=evidence_id,
        task_id=task_id,
        subject_sha=SHA,
        validator_id="contract-tests",
        validator_version="1",
        result=EvidenceResult.PASS,
        observed_at=NOW,
        artifact_sha256=("b" * 64,),
        summary="contract suite passed",
    )
    assert evidence.subject_sha == SHA
    assert evidence.observed_at.tzinfo == timezone.utc


def test_worker_lease_requires_positive_fencing_and_future_expiry() -> None:
    lease_id, task_id, attempt_id, runner_id = ids(4)
    with pytest.raises(ValidationError):
        WorkerLease(
            lease_id=lease_id,
            task_id=task_id,
            attempt_id=attempt_id,
            runner_id=runner_id,
            fencing_token=0,
            acquired_at=NOW,
            heartbeat_interval_seconds=15,
            expires_at=NOW + timedelta(seconds=30),
        )

    with pytest.raises(ValidationError, match="later than acquired_at"):
        WorkerLease(
            lease_id=lease_id,
            task_id=task_id,
            attempt_id=attempt_id,
            runner_id=runner_id,
            fencing_token=1,
            acquired_at=NOW,
            heartbeat_interval_seconds=15,
            expires_at=NOW,
        )


def test_scope_rejects_exact_protected_write_overlap() -> None:
    with pytest.raises(ValidationError, match="overlap"):
        Scope(write=("LICENSE.md",), protected=("LICENSE.md",))
