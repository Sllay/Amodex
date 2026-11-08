"""Product-facing contracts for isolated environments and disposable runners."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Literal

from pydantic import Field, field_validator

from amodex.contracts.base import ContractModel, require_utc
from amodex.contracts.ids import UUIDv7Str, validate_uuid7
from amodex.providers.budget import TaskBudgetClass


class RunnerKind(StrEnum):
    ACTIONS_HEADLESS = "ACTIONS_HEADLESS"
    DESKTOP_STUDIO = "DESKTOP_STUDIO"


class RunnerJobState(StrEnum):
    QUEUED = "QUEUED"
    DISPATCHED = "DISPATCHED"
    DISPATCH_UNKNOWN = "DISPATCH_UNKNOWN"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


def require_idempotency_key(value: str) -> str:
    """Opaque, case-sensitive key supplied by the intention's caller, never a secret."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value):
        raise ValueError("Idempotency-Key must contain 1-128 ASCII letters, digits, '.', '_', ':' or '-'")
    return value


class EnvironmentCreateRequest(ContractModel):
    schema_version: int = 1
    name: str = Field(min_length=1, max_length=200)
    engine: str | None = Field(default=None, max_length=100)


class ProjectBindRequest(ContractModel):
    schema_version: int = 1
    project_id: UUIDv7Str


class SessionCreateRequest(ContractModel):
    schema_version: int = 1
    title: str = Field(min_length=1, max_length=300)
    domain_key: str | None = Field(default=None, max_length=200)


class MemoryCreateRequest(ContractModel):
    schema_version: int = 1
    scope: str = Field(pattern=r"^(OWNER|ENVIRONMENT|DOMAIN|SESSION)$")
    content: str = Field(min_length=1, max_length=20000)
    source_ref: str = Field(min_length=1, max_length=1000)
    session_id: UUIDv7Str | None = None
    domain_key: str | None = Field(default=None, max_length=200)


class RunnerDispatchRequest(ContractModel):
    schema_version: Literal[1] = 1
    run_id: UUIDv7Str
    session_id: UUIDv7Str
    worker_profile: str = Field(default="implementation", min_length=1, max_length=200)
    task_class: TaskBudgetClass = TaskBudgetClass.STANDARD
    write_paths: tuple[str, ...] = ()

    @field_validator("write_paths")
    @classmethod
    def validate_write_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate write paths are not allowed")
        for path in value:
            clean = path.strip().replace("\\", "/")
            if not clean or clean.startswith("/") or clean.startswith("../") or "/../" in clean:
                raise ValueError(f"invalid relative write path: {path!r}")
        return value


class RunnerJobRecord(ContractModel):
    schema_version: int = 1
    job_id: UUIDv7Str
    environment_id: UUIDv7Str
    session_id: UUIDv7Str
    run_id: UUIDv7Str
    project_id: UUIDv7Str
    engine: str | None = None
    runner_kind: RunnerKind
    worker_profile: str
    task_class: TaskBudgetClass
    write_paths: tuple[str, ...] = ()
    state: RunnerJobState
    branch_name: str | None = None
    result_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    summary: str | None = Field(default=None, max_length=4000)
    github_run_id: str | None = Field(default=None, max_length=100)
    created_at: datetime
    updated_at: datetime
    claimed_at: datetime | None = None
    completed_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None
    heartbeat_count: int = Field(default=0, ge=0)
    dispatch_attempt_id: UUIDv7Str | None = None
    dispatch_status: str | None = None
    dispatch_target: dict[str, str] | None = None
    dispatch_started_at: datetime | None = None
    dispatch_finished_at: datetime | None = None
    dispatch_error_code: str | None = None
    execution_attempt_id: UUIDv7Str | None = None
    terminal_receipt_id: UUIDv7Str | None = None

    @field_validator("created_at", "updated_at", "claimed_at", "completed_at", "last_heartbeat_at", "lease_expires_at",
                     "dispatch_started_at", "dispatch_finished_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None, info):
        if value is None:
            return None
        return require_utc(value, field_name=info.field_name)


class RunnerClaimResponse(ContractModel):
    schema_version: int = 1
    job: RunnerJobRecord
    environment_name: str
    repo_url: str
    objective: str
    capabilities: tuple[str, ...]


def require_github_run_id(value: str) -> str:
    """An opaque caller-supplied workflow ID, never a cryptographic identity."""
    if (not isinstance(value, str) or not 1 <= len(value) <= 100
            or not value.isprintable() or any(char.isspace() for char in value)):
        raise ValueError("github_run_id must be 1-100 printable characters without whitespace")
    return value


class RunnerJobIdentityRequest(ContractModel):
    schema_version: int = 1
    github_run_id: str = Field(min_length=1, max_length=100)

    @field_validator("github_run_id")
    @classmethod
    def workflow_identity(cls, value: str) -> str:
        return require_github_run_id(value)


class RunnerJobHeartbeatRequest(RunnerJobIdentityRequest):
    pass


class RunnerReportRequest(RunnerJobIdentityRequest):
    schema_version: Literal[1] = 1
    attempt_id: UUIDv7Str
    state: RunnerJobState
    summary: str = Field(min_length=1, max_length=4000)
    result_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    evidence: dict[str, Any] = Field(default_factory=dict)

    @field_validator("state")
    @classmethod
    def terminal_only(cls, value: RunnerJobState) -> RunnerJobState:
        if value not in {RunnerJobState.COMPLETED, RunnerJobState.FAILED, RunnerJobState.CANCELLED}:
            raise ValueError("runner report state must be terminal")
        return value

    def digest(self, job_id: str) -> str:
        # Hash the complete normalized intent, BEFORE redacting presentation text.
        value = {"job_id": validate_uuid7(job_id), **self.model_dump(mode="json")}
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode()).hexdigest()

    def safe_evidence(self) -> dict[str, Any]:
        """A small evidence contract, not an arbitrary log/header storage channel."""
        if len(json.dumps(self.evidence, allow_nan=False)) > 8192:
            raise ValueError("REPORT_EVIDENCE_INVALID")
        for key, value in self.evidence.items():
            valid = False
            if key in {"godot", "mcp", "godot_import", "godot_mcp_probe"}:
                valid = type(value) is bool
            elif key in {"checks", "score"}:
                valid = type(value) is int and -1_000_000_000 <= value <= 1_000_000_000
            elif key == "source_sha":
                valid = isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None
            elif key == "runner":
                valid = isinstance(value, str) and value in {"github-actions", "desktop", "deterministic", "local-test"}
            elif key == "engine":
                valid = isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_. -]{1,100}", value) is not None
            elif key == "github_run_id":
                valid = value == self.github_run_id
            elif key == "references":
                valid = isinstance(value, list) and len(value) <= 20 and all(
                    isinstance(ref, str) and re.fullmatch(r"(?:evidence|artifact)://[A-Za-z0-9][A-Za-z0-9_./-]{0,199}", ref)
                    and ".." not in ref.split("/") for ref in value)
            if not valid:
                raise ValueError("REPORT_EVIDENCE_INVALID")
        return self.evidence

    def safe_summary(self) -> str:
        text = "".join(c for c in self.summary if c.isprintable())
        text = re.sub(r"(?i)bearer\s+\S+", "[REDACTED]", text)
        text = re.sub(r"(?i)(?:authorization|x-api-key|token|secret|password)\s*[:=].*", "[REDACTED]", text)
        return re.sub(r"https?://\S+", "[URL REDACTED]", text)[:4000]


class RunnerTerminalReceipt(ContractModel):
    schema_version: Literal[1] = 1
    receipt_id: UUIDv7Str
    job_id: UUIDv7Str
    attempt_id: UUIDv7Str
    github_run_id: str
    report_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: RunnerJobState
    summary: str
    result_commit: str | None
    evidence: dict[str, Any]
    audit_id: UUIDv7Str
    environment_id: UUIDv7Str
    session_id: UUIDv7Str
    run_id: UUIDv7Str
    committed_at: datetime


class UsageProjection(ContractModel):
    schema_version: int = 1
    environment_id: UUIDv7Str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    requests: int = Field(ge=0)
    by_provider: list[dict[str, Any]] = Field(default_factory=list)


def runner_kind_for_engine(engine: str | None) -> RunnerKind:
    normalized = (engine or "").strip().lower()
    if normalized in {"roblox", "roblox studio", "roblox-studio"}:
        return RunnerKind.DESKTOP_STUDIO
    return RunnerKind.ACTIONS_HEADLESS


def capabilities_for_engine(engine: str | None) -> tuple[str, ...]:
    normalized = (engine or "").strip().lower()
    if normalized == "godot":
        return (
            "repository.read",
            "repository.write",
            "runner.actions-headless",
            "godot.headless",
            "godot.mcp-headless",
            "validate",
        )
    if normalized in {"roblox", "roblox studio", "roblox-studio"}:
        return (
            "repository.read",
            "repository.write",
            "runner.desktop-studio",
            "roblox.studio-mcp",
            "validate",
        )
    return ("repository.read", "repository.write", "runner.actions-headless", "validate")
