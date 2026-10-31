"""Executable subset of the normative Amodex runtime contracts (schema v1)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from .base import ContractModel, require_utc
from .ids import UUIDv7Str

Sha40 = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
NonEmptyStr = Annotated[str, Field(min_length=1)]
PositiveInt = Annotated[int, Field(gt=0)]
SchemaV1 = Literal[1]


class SideEffectClass(StrEnum):
    READ_ONLY = "read_only"
    IDEMPOTENT_WRITE = "idempotent_write"
    CONDITIONALLY_IDEMPOTENT = "conditionally_idempotent"
    NON_IDEMPOTENT = "non_idempotent"


class CheckpointClass(StrEnum):
    WIP = "WIP"
    SAFE = "SAFE"
    VALIDATED = "VALIDATED"


class LeaseStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class EvidenceResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


class Objective(ContractModel):
    statement: NonEmptyStr
    non_goals: tuple[str, ...] = ()


class RepositoryAuthority(ContractModel):
    repository_id: NonEmptyStr
    expected_base_sha: Sha40
    task_branch: NonEmptyStr


class Scope(ContractModel):
    write: tuple[str, ...] = ()
    read: tuple[str, ...] = ()
    protected: tuple[str, ...] = ()
    allowed_operations: tuple[str, ...] = ()

    @field_validator("write", "read", "protected", "allowed_operations")
    @classmethod
    def reject_duplicates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate entries are not allowed")
        return value

    @model_validator(mode="after")
    def reject_exact_write_protected_overlap(self) -> "Scope":
        overlap = set(self.write) & set(self.protected)
        if overlap:
            raise ValueError(f"write and protected scopes overlap: {sorted(overlap)!r}")
        return self


class Authority(ContractModel):
    may_delegate: bool = False
    may_request_scope_expansion: bool = True
    may_commit_checkpoint: bool = True
    may_push_task_branch: bool = True
    may_create_pr: bool = False
    may_promote: bool = False


class TaskPackage(ContractModel):
    IDENTITY_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"package_id", "task_id", "attempt_id"}
    )

    schema_version: SchemaV1 = 1
    package_id: UUIDv7Str
    task_id: UUIDv7Str
    attempt_id: UUIDv7Str
    package_revision: PositiveInt = 1
    issued_at: datetime
    expires_at: datetime
    objective: Objective
    repository: RepositoryAuthority
    scope: Scope
    required_capabilities: tuple[NonEmptyStr, ...] = ()
    optional_capabilities: tuple[NonEmptyStr, ...] = ()
    authority: Authority = Field(default_factory=Authority)

    @field_validator("issued_at", "expires_at")
    @classmethod
    def normalize_datetime(cls, value: datetime, info):
        return require_utc(value, field_name=info.field_name)

    @model_validator(mode="after")
    def validate_lifetime_and_capabilities(self) -> "TaskPackage":
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be later than issued_at")
        overlap = set(self.required_capabilities) & set(self.optional_capabilities)
        if overlap:
            raise ValueError(f"capability cannot be both required and optional: {sorted(overlap)!r}")
        return self


class WorkerLease(ContractModel):
    IDENTITY_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"lease_id", "task_id", "attempt_id", "runner_id"}
    )

    schema_version: SchemaV1 = 1
    lease_id: UUIDv7Str
    task_id: UUIDv7Str
    attempt_id: UUIDv7Str
    runner_id: UUIDv7Str
    fencing_token: PositiveInt
    status: LeaseStatus = LeaseStatus.ACTIVE
    acquired_at: datetime
    heartbeat_interval_seconds: PositiveInt
    expires_at: datetime

    @field_validator("acquired_at", "expires_at")
    @classmethod
    def normalize_datetime(cls, value: datetime, info):
        return require_utc(value, field_name=info.field_name)

    @model_validator(mode="after")
    def validate_expiry(self) -> "WorkerLease":
        if self.expires_at <= self.acquired_at:
            raise ValueError("expires_at must be later than acquired_at")
        return self


class ToolCall(ContractModel):
    IDENTITY_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"tool_call_id", "task_id", "attempt_id"}
    )

    schema_version: SchemaV1 = 1
    tool_call_id: UUIDv7Str
    task_id: UUIDv7Str
    attempt_id: UUIDv7Str
    tool_name: NonEmptyStr
    arguments: dict[str, object] = Field(default_factory=dict)
    side_effect_class: SideEffectClass
    idempotency_key: str | None = None
    expected_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_side_effect_guards(self) -> "ToolCall":
        if self.side_effect_class != SideEffectClass.READ_ONLY and not self.idempotency_key:
            raise ValueError("write tool calls require an idempotency_key")
        if (
            self.side_effect_class == SideEffectClass.CONDITIONALLY_IDEMPOTENT
            and self.expected_revision is None
        ):
            raise ValueError("conditionally idempotent calls require expected_revision")
        return self


class ValidationEvidence(ContractModel):
    IDENTITY_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"evidence_id", "task_id", "subject_sha"}
    )

    schema_version: SchemaV1 = 1
    evidence_id: UUIDv7Str
    task_id: UUIDv7Str
    subject_sha: Sha40
    validator_id: NonEmptyStr
    validator_version: NonEmptyStr
    result: EvidenceResult
    observed_at: datetime
    artifact_sha256: tuple[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")], ...] = ()
    summary: NonEmptyStr

    @field_validator("observed_at")
    @classmethod
    def normalize_datetime(cls, value: datetime):
        return require_utc(value, field_name="observed_at")


class Checkpoint(ContractModel):
    IDENTITY_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"checkpoint_id", "task_id", "attempt_id", "commit_sha"}
    )

    schema_version: SchemaV1 = 1
    checkpoint_id: UUIDv7Str
    task_id: UUIDv7Str
    attempt_id: UUIDv7Str
    checkpoint_class: CheckpointClass
    commit_sha: Sha40
    created_at: datetime
    evidence_ids: tuple[UUIDv7Str, ...] = ()

    @field_validator("created_at")
    @classmethod
    def normalize_datetime(cls, value: datetime):
        return require_utc(value, field_name="created_at")

    @model_validator(mode="after")
    def validated_checkpoint_requires_evidence(self) -> "Checkpoint":
        if self.checkpoint_class == CheckpointClass.VALIDATED and not self.evidence_ids:
            raise ValueError("VALIDATED checkpoints require at least one evidence_id")
        return self


CONTRACT_TYPES: dict[str, type[ContractModel]] = {
    "task-package": TaskPackage,
    "worker-lease": WorkerLease,
    "tool-call": ToolCall,
    "checkpoint": Checkpoint,
    "validation-evidence": ValidationEvidence,
}
