"""Stage 6 product-facing contracts.

These objects project authoritative runtime state into CLI/web surfaces. They do
not grant repository/provider/promotion authority by themselves.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator

from amodex.contracts.base import ContractModel, require_utc
from amodex.contracts.ids import UUIDv7Str


class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class RunDesiredState(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class RunnerState(StrEnum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProjectRecord(ContractModel):
    schema_version: int = 1
    project_id: UUIDv7Str
    name: str = Field(min_length=1, max_length=200)
    repo_url: str = Field(min_length=1, max_length=2000)
    status: ProjectStatus = ProjectStatus.ACTIVE
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_datetime(cls, value: datetime, info):
        return require_utc(value, field_name=info.field_name)


class ProjectCreateRequest(ContractModel):
    schema_version: int = 1
    repo_url: str = Field(min_length=1, max_length=2000)
    name: str | None = Field(default=None, max_length=200)


class RunControl(ContractModel):
    schema_version: int = 1
    run_id: UUIDv7Str
    desired_state: RunDesiredState
    reason: str | None = Field(default=None, max_length=1000)
    requested_at: datetime
    updated_at: datetime

    @field_validator("requested_at", "updated_at")
    @classmethod
    def normalize_datetime(cls, value: datetime, info):
        return require_utc(value, field_name=info.field_name)


class RunControlRequest(ContractModel):
    schema_version: int = 1
    reason: str | None = Field(default=None, max_length=1000)


class ApprovalRecord(ContractModel):
    schema_version: int = 1
    approval_id: UUIDv7Str
    action: str = Field(min_length=1, max_length=200)
    subject_type: str = Field(min_length=1, max_length=100)
    subject_id: str = Field(min_length=1, max_length=500)
    impact: str = Field(min_length=1, max_length=4000)
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime
    resolved_at: datetime | None = None
    expires_at: datetime | None = None

    @field_validator("requested_at", "resolved_at", "expires_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None, info):
        if value is None:
            return None
        return require_utc(value, field_name=info.field_name)


class ApprovalCreateRequest(ContractModel):
    schema_version: int = 1
    action: str = Field(min_length=1, max_length=200)
    subject_type: str = Field(min_length=1, max_length=100)
    subject_id: str = Field(min_length=1, max_length=500)
    impact: str = Field(min_length=1, max_length=4000)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def normalize_expiry(cls, value: datetime | None):
        if value is None:
            return None
        return require_utc(value, field_name="expires_at")


class ProviderConnection(ContractModel):
    schema_version: int = 1
    provider: str = Field(min_length=1, max_length=100)
    credential_pool_id: str = Field(min_length=1, max_length=200)
    secret_ref: str = Field(min_length=1, max_length=300)
    billing_class: str = Field(pattern=r"^(free|paid|unknown)$")
    enabled: bool = True
    secret_present: bool = False
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def normalize_datetime(cls, value: datetime):
        return require_utc(value, field_name="updated_at")


class ProviderConnectionRequest(ContractModel):
    schema_version: int = 1
    provider: str = Field(min_length=1, max_length=100)
    credential_pool_id: str = Field(min_length=1, max_length=200)
    secret_ref: str = Field(min_length=1, max_length=300)
    billing_class: str = Field(pattern=r"^(free|paid|unknown)$")
    enabled: bool = True


class RunnerRecord(ContractModel):
    schema_version: int = 1
    runner_id: str = Field(min_length=1, max_length=200)
    profile: str = Field(min_length=1, max_length=200)
    state: RunnerState
    capabilities: tuple[str, ...] = ()
    last_heartbeat_at: datetime
    detail: str | None = Field(default=None, max_length=2000)

    @field_validator("last_heartbeat_at")
    @classmethod
    def normalize_datetime(cls, value: datetime):
        return require_utc(value, field_name="last_heartbeat_at")


class RunnerHeartbeatRequest(ContractModel):
    schema_version: int = 1
    profile: str = Field(min_length=1, max_length=200)
    state: RunnerState
    capabilities: tuple[str, ...] = ()
    detail: str | None = Field(default=None, max_length=2000)


class AuditEvent(ContractModel):
    schema_version: int = 1
    audit_id: UUIDv7Str
    actor: str = Field(min_length=1, max_length=200)
    action: str = Field(min_length=1, max_length=200)
    subject_type: str = Field(min_length=1, max_length=100)
    subject_id: str = Field(min_length=1, max_length=500)
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def normalize_datetime(cls, value: datetime):
        return require_utc(value, field_name="created_at")


class TaskView(ContractModel):
    schema_version: int = 1
    run_id: UUIDv7Str
    task_id: str = Field(min_length=1, max_length=300)
    objective: str = Field(min_length=1, max_length=4000)
    state: str = Field(min_length=1, max_length=100)
    dependencies: tuple[str, ...] = ()
    worker_profile: str | None = Field(default=None, max_length=200)
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def normalize_datetime(cls, value: datetime):
        return require_utc(value, field_name="updated_at")


class EvidenceView(ContractModel):
    schema_version: int = 1
    evidence_id: str = Field(min_length=1, max_length=300)
    run_id: UUIDv7Str | None = None
    task_id: str = Field(min_length=1, max_length=300)
    subject_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    validator_id: str = Field(min_length=1, max_length=300)
    result: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=4000)
    artifact_sha256: tuple[str, ...] = ()
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def normalize_datetime(cls, value: datetime):
        return require_utc(value, field_name="observed_at")


class CheckpointView(ContractModel):
    schema_version: int = 1
    checkpoint_id: str = Field(min_length=1, max_length=300)
    run_id: UUIDv7Str | None = None
    task_id: str = Field(min_length=1, max_length=300)
    checkpoint_class: str = Field(min_length=1, max_length=100)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    evidence_ids: tuple[str, ...] = ()
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def normalize_datetime(cls, value: datetime):
        return require_utc(value, field_name="created_at")


class DashboardSnapshot(ContractModel):
    schema_version: int = 1
    generated_at: datetime
    release: str
    zero_cost_mode: bool
    projects: list[dict[str, Any]] = Field(default_factory=list)
    runs: list[dict[str, Any]] = Field(default_factory=list)
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    providers: list[dict[str, Any]] = Field(default_factory=list)
    runners: list[dict[str, Any]] = Field(default_factory=list)
    checkpoints: list[dict[str, Any]] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    promotions: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    blockers: list[dict[str, Any]] = Field(default_factory=list)
    timeline: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("generated_at")
    @classmethod
    def normalize_datetime(cls, value: datetime):
        return require_utc(value, field_name="generated_at")
