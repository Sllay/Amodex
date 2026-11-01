"""Stage 5 worker-profile and model-routing contracts.

Profiles describe bounded capabilities/context policy. They never grant repository,
provider, cost, or promotion authority by themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class WorkerProfileKind(StrEnum):
    REPOSITORY_ANALYST = "repository-analyst"
    RESEARCH = "research"
    IMPLEMENTATION = "implementation"
    CORRECTION = "correction"
    REVIEWER = "reviewer"
    INTEGRATION = "integration-migration"
    GODOT = "godot-specialist"
    VISUAL_RUNTIME = "visual-runtime-validation"


class ModelCapability(StrEnum):
    TEXT = "text"
    STRUCTURED_OUTPUT = "structured-output"
    TOOL_CALLING = "tool-calling"
    CODING = "coding"
    CORRECTION = "correction"
    PLANNING = "planning"
    RESEARCH = "research"
    REVIEW = "review"
    VISION = "vision"
    LONG_CONTEXT = "long-context"


class BillingClass(StrEnum):
    FREE = "free"
    PAID = "paid"
    UNKNOWN = "unknown"


class ProviderHealth(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    LIMITED = "LIMITED"
    UNAVAILABLE = "UNAVAILABLE"
    DISABLED_POLICY = "DISABLED_POLICY"


class ModelLifecycle(StrEnum):
    DISCOVERED = "DISCOVERED"
    PROFILED_DECLARED = "PROFILED_DECLARED"
    BENCHMARK_PENDING = "BENCHMARK_PENDING"
    ELIGIBLE_LIMITED = "ELIGIBLE_LIMITED"
    ELIGIBLE = "ELIGIBLE"


@dataclass(frozen=True, slots=True)
class WorkerProfile:
    profile_id: WorkerProfileKind
    model_requirements: frozenset[ModelCapability]
    allowed_context_channels: frozenset[str]
    read_only: bool
    description: str


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    provider: str
    model: str
    credential_pool_id: str
    cost_class: BillingClass
    capabilities: frozenset[ModelCapability]
    context_capacity_tokens: int
    lifecycle: ModelLifecycle
    provenance: str
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("provider/model must be non-empty")
        if not self.credential_pool_id.strip():
            raise ValueError("credential_pool_id must be non-empty")
        if self.context_capacity_tokens < 1:
            raise ValueError("context_capacity_tokens must be positive")

    @property
    def route_id(self) -> str:
        return f"{self.provider}:{self.model}:{self.credential_pool_id}"


@dataclass(frozen=True, slots=True)
class ModelMeasurement:
    provider: str
    model: str
    sample_count: int
    structured_tool_reliability: float
    coding_pass_rate: float
    correction_success_rate: float
    planning_schema_compliance: float
    research_success_rate: float
    review_detection_rate: float
    vision_success_rate: float | None
    latency_ms_p50: float
    observed_at: datetime
    source: str

    def __post_init__(self) -> None:
        if self.sample_count < 1:
            raise ValueError("sample_count must be positive")
        if self.latency_ms_p50 < 0:
            raise ValueError("latency_ms_p50 cannot be negative")
        for name in (
            "structured_tool_reliability",
            "coding_pass_rate",
            "correction_success_rate",
            "planning_schema_compliance",
            "research_success_rate",
            "review_detection_rate",
        ):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.vision_success_rate is not None and not 0.0 <= self.vision_success_rate <= 1.0:
            raise ValueError("vision_success_rate must be between 0 and 1")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if not self.source.strip():
            raise ValueError("measurement source must be non-empty")

    def quality_for(self, profile: WorkerProfileKind) -> float:
        if profile == WorkerProfileKind.RESEARCH:
            return self.research_success_rate
        if profile in {WorkerProfileKind.IMPLEMENTATION, WorkerProfileKind.GODOT}:
            return self.coding_pass_rate
        if profile == WorkerProfileKind.CORRECTION:
            return self.correction_success_rate
        if profile == WorkerProfileKind.REVIEWER:
            return self.review_detection_rate
        if profile == WorkerProfileKind.VISUAL_RUNTIME:
            return self.vision_success_rate if self.vision_success_rate is not None else 0.0
        return self.planning_schema_compliance


@dataclass(frozen=True, slots=True)
class ProviderAvailability:
    provider: str
    credential_pool_id: str
    billing_class: BillingClass
    health: ProviderHealth
    quota_available: bool
    free_capacity_ratio: float
    observed_at: datetime
    reason: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.free_capacity_ratio <= 1.0:
            raise ValueError("free_capacity_ratio must be between 0 and 1")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class RoutingRequest:
    profile_id: WorkerProfileKind
    required_capabilities: frozenset[ModelCapability] = frozenset()
    required_context_tokens: int = 1
    provider_denylist: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.required_context_tokens < 1:
            raise ValueError("required_context_tokens must be positive")


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    profile_id: WorkerProfileKind
    route: ModelCandidate
    score: float
    ranked_route_ids: tuple[str, ...]
    rejected_reasons: tuple[str, ...]
    selection_facts: tuple[str, ...]


class RoutingUnavailable(RuntimeError):
    def __init__(self, reasons: tuple[str, ...]) -> None:
        super().__init__("no eligible zero-cost measured route")
        self.reasons = reasons


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stage5_worker_profiles() -> dict[WorkerProfileKind, WorkerProfile]:
    return {
        WorkerProfileKind.REPOSITORY_ANALYST: WorkerProfile(
            WorkerProfileKind.REPOSITORY_ANALYST,
            frozenset({ModelCapability.PLANNING, ModelCapability.TEXT}),
            frozenset({"contract", "repository-map", "focused-docs", "symbols"}),
            True,
            "Read-only repository structure and authority analysis.",
        ),
        WorkerProfileKind.RESEARCH: WorkerProfile(
            WorkerProfileKind.RESEARCH,
            frozenset({ModelCapability.RESEARCH, ModelCapability.TEXT}),
            frozenset({"contract", "research-question", "focused-docs", "public-sources"}),
            True,
            "Read-only evidence gathering with provenance.",
        ),
        WorkerProfileKind.IMPLEMENTATION: WorkerProfile(
            WorkerProfileKind.IMPLEMENTATION,
            frozenset({ModelCapability.CODING, ModelCapability.TOOL_CALLING, ModelCapability.TEXT}),
            frozenset({"contract", "context-capsule", "source", "tests", "accepted-decisions"}),
            False,
            "Scoped implementation under TaskPackage ownership.",
        ),
        WorkerProfileKind.CORRECTION: WorkerProfile(
            WorkerProfileKind.CORRECTION,
            frozenset({ModelCapability.CORRECTION, ModelCapability.CODING, ModelCapability.TEXT}),
            frozenset({"contract", "context-capsule", "diff", "failing-evidence", "tests"}),
            False,
            "Bounded correction after deterministic failure evidence.",
        ),
        WorkerProfileKind.REVIEWER: WorkerProfile(
            WorkerProfileKind.REVIEWER,
            frozenset({ModelCapability.REVIEW, ModelCapability.TEXT}),
            frozenset({"contract", "diff", "evidence", "acceptance-criteria"}),
            True,
            "Independent review; implementation transcript is deliberately excluded.",
        ),
        WorkerProfileKind.INTEGRATION: WorkerProfile(
            WorkerProfileKind.INTEGRATION,
            frozenset({ModelCapability.PLANNING, ModelCapability.CODING, ModelCapability.TEXT}),
            frozenset({"contract", "diff", "dependency-interfaces", "migration-plan", "tests"}),
            False,
            "Scoped integration/migration work; no promotion authority.",
        ),
        WorkerProfileKind.GODOT: WorkerProfile(
            WorkerProfileKind.GODOT,
            frozenset({ModelCapability.CODING, ModelCapability.TOOL_CALLING, ModelCapability.TEXT}),
            frozenset({"contract", "context-capsule", "godot-project", "runtime-diagnostics", "tests"}),
            False,
            "Godot/GDScript specialist profile; engine tools remain capability-scoped.",
        ),
        WorkerProfileKind.VISUAL_RUNTIME: WorkerProfile(
            WorkerProfileKind.VISUAL_RUNTIME,
            frozenset({ModelCapability.VISION, ModelCapability.TEXT}),
            frozenset({"contract", "acceptance-criteria", "runtime-diagnostics", "screenshots", "evidence"}),
            True,
            "Read-only visual/runtime validation; screenshots complement executable evidence.",
        ),
    }
