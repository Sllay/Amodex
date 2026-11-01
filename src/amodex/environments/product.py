"""Product-facing contracts for isolated environments and disposable runners."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator

from amodex.contracts.base import ContractModel, require_utc
from amodex.contracts.ids import UUIDv7Str
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
    schema_version: int = 1
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

    @field_validator("created_at", "updated_at", "claimed_at", "completed_at")
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


class RunnerReportRequest(ContractModel):
    schema_version: int = 1
    state: RunnerJobState
    summary: str = Field(min_length=1, max_length=4000)
    result_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    github_run_id: str | None = Field(default=None, max_length=100)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @field_validator("state")
    @classmethod
    def terminal_only(cls, value: RunnerJobState) -> RunnerJobState:
        if value not in {RunnerJobState.COMPLETED, RunnerJobState.FAILED, RunnerJobState.CANCELLED}:
            raise ValueError("runner report state must be terminal")
        return value


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
