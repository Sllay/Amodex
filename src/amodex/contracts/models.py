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


class CandidateStatus(StrEnum):
    READY = "READY"
    STALE = "STALE"
    BLOCKED = "BLOCKED"
    PROMOTED = "PROMOTED"


class PromotionStatus(StrEnum):
    INTENT_PERSISTED = "INTENT_PERSISTED"
    INTEGRATION_VALIDATED = "INTEGRATION_VALIDATED"
    REMOTE_APPLIED = "REMOTE_APPLIED"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    PROMOTED = "PROMOTED"
    BLOCKED = "BLOCKED"


class PromotionMethod(StrEnum):
    MERGE = "MERGE"
    FAST_FORWARD = "FAST_FORWARD"
    REVERT = "REVERT"


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


class Candidate(ContractModel):
    """Durable promotion candidate bound to a VALIDATED source SHA and target SHA."""

    IDENTITY_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"candidate_id", "task_id", "validated_checkpoint_id", "source_sha"}
    )

    schema_version: SchemaV1 = 1
    candidate_id: UUIDv7Str
    task_id: UUIDv7Str
    validated_checkpoint_id: UUIDv7Str
    source_sha: Sha40
    expected_target_ref: NonEmptyStr
    expected_target_sha: Sha40
    changed_paths: tuple[NonEmptyStr, ...]
    dependency_paths: tuple[NonEmptyStr, ...] = ()
    evidence_ids: tuple[UUIDv7Str, ...]
    status: CandidateStatus = CandidateStatus.READY
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def normalize_datetime(cls, value: datetime):
        return require_utc(value, field_name="created_at")

    @field_validator("changed_paths", "dependency_paths", "evidence_ids")
    @classmethod
    def reject_duplicates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate candidate entries are not allowed")
        return value

    @model_validator(mode="after")
    def require_source_proof(self) -> "Candidate":
        if not self.changed_paths:
            raise ValueError("Candidate requires at least one changed path")
        if not self.evidence_ids:
            raise ValueError("Candidate requires source validation evidence")
        return self


class Promotion(ContractModel):
    """Durable promotion intent/result. PROMOTED is not a checkpoint class."""

    IDENTITY_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"promotion_id", "candidate_id", "idempotency_key"}
    )

    schema_version: SchemaV1 = 1
    promotion_id: UUIDv7Str
    candidate_id: UUIDv7Str
    idempotency_key: NonEmptyStr
    target_ref: NonEmptyStr
    expected_target_sha: Sha40
    method: PromotionMethod = PromotionMethod.MERGE
    status: PromotionStatus = PromotionStatus.INTENT_PERSISTED
    integration_sha: Sha40 | None = None
    target_post_sha: Sha40 | None = None
    integration_evidence_ids: tuple[UUIDv7Str, ...] = ()
    created_at: datetime
    updated_at: datetime
    reconciled_at: datetime | None = None
    detail: str | None = None

    @field_validator("created_at", "updated_at", "reconciled_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None, info):
        if value is None:
            return None
        return require_utc(value, field_name=info.field_name)

    @field_validator("integration_evidence_ids")
    @classmethod
    def reject_evidence_duplicates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate promotion evidence is not allowed")
        return value

    @model_validator(mode="after")
    def validate_result_shape(self) -> "Promotion":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot predate created_at")
        if self.status in {
            PromotionStatus.INTEGRATION_VALIDATED,
            PromotionStatus.REMOTE_APPLIED,
            PromotionStatus.PROMOTED,
        }:
            if self.integration_sha is None:
                raise ValueError(f"{self.status.value} requires integration_sha")
            if not self.integration_evidence_ids:
                raise ValueError(f"{self.status.value} requires integration validation evidence")
        if self.status in {PromotionStatus.REMOTE_APPLIED, PromotionStatus.PROMOTED}:
            if self.target_post_sha is None:
                raise ValueError(f"{self.status.value} requires target_post_sha")
        return self


CONTRACT_TYPES: dict[str, type[ContractModel]] = {
    "task-package": TaskPackage,
    "worker-lease": WorkerLease,
    "tool-call": ToolCall,
    "checkpoint": Checkpoint,
    "validation-evidence": ValidationEvidence,
    "candidate": Candidate,
    "promotion": Promotion,
}
