"""Stage 1 durable run/event models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from amodex.contracts.base import ContractModel
from amodex.contracts.ids import UUIDv7Str


class RunState(StrEnum):
    QUEUED = "QUEUED"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    WAITING_PROVIDER = "WAITING_PROVIDER"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    BLOCKED = "BLOCKED"


class ProviderRequestState(StrEnum):
    NONE = "NONE"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"


class EventKind(StrEnum):
    RUN_CREATED = "RUN_CREATED"
    RUN_CLAIMED = "RUN_CLAIMED"
    PROVIDER_ADMITTED = "PROVIDER_ADMITTED"
    PROVIDER_WAIT = "PROVIDER_WAIT"
    PROVIDER_REQUEST_STARTED = "PROVIDER_REQUEST_STARTED"
    PROVIDER_REQUEST_COMPLETED = "PROVIDER_REQUEST_COMPLETED"
    PROVIDER_OUTCOME_UNKNOWN = "PROVIDER_OUTCOME_UNKNOWN"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_FAILED = "RUN_FAILED"
    LEASE_EXPIRED_RECLAIMED = "LEASE_EXPIRED_RECLAIMED"


class RunRecord(ContractModel):
    schema_version: int = 1
    run_id: UUIDv7Str
    prompt: str = Field(min_length=1, max_length=20000)
    state: RunState
    provider: str | None = None
    model: str | None = None
    result_text: str | None = None
    last_error_code: str | None = None
    provider_request_key: UUIDv7Str | None = None
    provider_request_state: ProviderRequestState = ProviderRequestState.NONE
    revision: int = Field(ge=1)
    fencing_token: int = Field(ge=0)
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class RunEvent(ContractModel):
    schema_version: int = 1
    event_id: UUIDv7Str
    run_id: UUIDv7Str
    seq: int = Field(gt=0)
    kind: EventKind
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CreateRunRequest(ContractModel):
    schema_version: int = 1
    prompt: str = Field(min_length=1, max_length=20000)


class ProviderUsage(ContractModel):
    schema_version: int = 1
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class ProviderResponse(ContractModel):
    schema_version: int = 1
    provider: str
    model: str
    text: str
    usage: ProviderUsage = Field(default_factory=ProviderUsage)
