"""Contracts for Step 4 benchmark, lifecycle and routed chat inference."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from amodex.providers.capabilities import ModelCapability, WorkerProfileKind


class BenchmarkStatus(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    BLOCKED = "BLOCKED"


class DecisionStatus(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    BLOCKED = "BLOCKED"


class AttemptStatus(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


class BenchmarkRunRequest(BaseModel):
    route_id: str = Field(min_length=3, max_length=1000)
    force: bool = False


class ChatRespondRequest(BaseModel):
    owner_message_id: str | None = Field(default=None, min_length=1, max_length=100)
    profile: WorkerProfileKind | None = None


class ReconcileRequest(BaseModel):
    stale_after_seconds: int = Field(default=300, ge=60, le=86400)


@dataclass(frozen=True, slots=True)
class BenchmarkCaseSpec:
    case_id: str
    capability: ModelCapability
    prompt: str
    expected: str
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class RankedRoute:
    route_id: str
    provider: str
    model: str
    lifecycle: str
    score: float
    base_score: float
    progress_per_1k_tokens: float
    selection_facts: tuple[str, ...]
    rejected_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "provider": self.provider,
            "model": self.model,
            "lifecycle": self.lifecycle,
            "score": self.score,
            "base_score": self.base_score,
            "progress_per_1k_tokens": self.progress_per_1k_tokens,
            "selection_facts": list(self.selection_facts),
            "rejected_reasons": list(self.rejected_reasons),
        }


class ModelIntelligenceError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        facts: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.facts = facts

    def detail(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "facts": list(self.facts)}
