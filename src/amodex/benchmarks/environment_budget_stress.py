"""Deterministic environment/quota stress benchmark.

This benchmark intentionally does NOT call live providers. Burning free-provider
quota to test a quota governor would defeat the purpose. Instead it drives the
real routing/budget policy with measured-style fixtures, forced provider outages,
capacity depletion and cross-environment access attempts.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from amodex.environments.models import EnvironmentCapability, EnvironmentGrant
from amodex.environments.policy import EnvironmentBoundary, EnvironmentBoundaryError
from amodex.providers.budget import (
    AdaptiveRouteSession,
    BudgetAwareRouter,
    BudgetRoutingUnavailable,
    BudgetedRoutingRequest,
    BudgetExceeded,
    QuotaBudgetGovernor,
    TaskBudgetClass,
)
from amodex.providers.capabilities import (
    BillingClass,
    ModelCandidate,
    ModelCapability,
    ModelLifecycle,
    ModelMeasurement,
    ProviderAvailability,
    ProviderHealth,
    RoutingRequest,
    WorkerProfileKind,
)
from amodex.providers.routing import CapabilityRouter


@dataclass(frozen=True, slots=True)
class StressMetrics:
    requested_tasks: int
    completed_tasks: int
    blocked_tasks: int
    cross_environment_blocks: int
    total_input_tokens: int
    total_output_tokens: int
    provider_selections: dict[str, int]
    provider_failover_events: int
    paid_route_selections: int

    @property
    def completion_ratio(self) -> float:
        return self.completed_tasks / max(self.requested_tasks, 1)

    @property
    def tasks_per_million_tokens(self) -> float:
        total = self.total_input_tokens + self.total_output_tokens
        return self.completed_tasks * 1_000_000 / max(total, 1)

    def as_json(self) -> dict[str, object]:
        payload = asdict(self)
        payload["completion_ratio"] = round(self.completion_ratio, 6)
        payload["tasks_per_million_tokens"] = round(self.tasks_per_million_tokens, 3)
        return payload


def _measurement(provider: str, model: str, quality: float, now: datetime) -> ModelMeasurement:
    return ModelMeasurement(
        provider=provider,
        model=model,
        sample_count=20,
        structured_tool_reliability=min(1.0, quality + 0.04),
        coding_pass_rate=quality,
        correction_success_rate=max(0.0, quality - 0.02),
        planning_schema_compliance=min(1.0, quality + 0.02),
        research_success_rate=max(0.0, quality - 0.03),
        review_detection_rate=quality,
        vision_success_rate=quality,
        latency_ms_p50=900.0,
        observed_at=now,
        source="postbaseline-stress-fixture",
    )


def _candidates() -> tuple[ModelCandidate, ...]:
    common = frozenset(
        {
            ModelCapability.TEXT,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.TOOL_CALLING,
            ModelCapability.CODING,
            ModelCapability.CORRECTION,
            ModelCapability.PLANNING,
            ModelCapability.RESEARCH,
            ModelCapability.REVIEW,
        }
    )
    return (
        ModelCandidate("groq", "stress-groq", "groq-free", BillingClass.FREE, common, 65_536, ModelLifecycle.ELIGIBLE, "stress-fixture"),
        ModelCandidate("openrouter", "stress-openrouter", "openrouter-free", BillingClass.FREE, common, 131_072, ModelLifecycle.ELIGIBLE, "stress-fixture"),
        ModelCandidate("nvidia", "stress-nvidia", "nvidia-free", BillingClass.FREE, common | frozenset({ModelCapability.VISION}), 131_072, ModelLifecycle.ELIGIBLE, "stress-fixture"),
        ModelCandidate("gemini", "stress-gemini", "gemini-free", BillingClass.FREE, common | frozenset({ModelCapability.VISION, ModelCapability.LONG_CONTEXT}), 1_000_000, ModelLifecycle.ELIGIBLE, "stress-fixture"),
        ModelCandidate("paid", "stress-paid-never", "paid", BillingClass.PAID, common | frozenset({ModelCapability.VISION, ModelCapability.LONG_CONTEXT}), 1_000_000, ModelLifecycle.ELIGIBLE, "stress-fixture"),
    )


def _task_shape(index: int) -> tuple[TaskBudgetClass, int, int]:
    selector = index % 20
    if selector < 6:
        return TaskBudgetClass.MICRO, 2_000, 700
    if selector < 13:
        return TaskBudgetClass.ROUTINE, 5_000, 1_500
    if selector < 17:
        return TaskBudgetClass.STANDARD, 12_000, 3_000
    if selector < 19:
        return TaskBudgetClass.COMPLEX, 36_000, 8_000
    return TaskBudgetClass.CRITICAL, 80_000, 16_000


def run_stress(task_count: int = 600) -> StressMetrics:
    if task_count < 1:
        raise ValueError("task_count must be positive")

    now = datetime.now(timezone.utc)
    candidates = _candidates()
    measurements = (
        _measurement("groq", "stress-groq", 0.80, now),
        _measurement("openrouter", "stress-openrouter", 0.70, now),
        _measurement("nvidia", "stress-nvidia", 0.91, now),
        _measurement("gemini", "stress-gemini", 0.76, now),
        _measurement("paid", "stress-paid-never", 0.99, now),
    )
    shared_caps = frozenset(
        {
            EnvironmentCapability.CONTEXT_READ,
            EnvironmentCapability.REPOSITORY_READ,
            EnvironmentCapability.REPOSITORY_WRITE,
            EnvironmentCapability.PROVIDER_USE,
            EnvironmentCapability.RUNNER_USE,
            EnvironmentCapability.VALIDATE,
        }
    )
    grants = (
        EnvironmentGrant("grant-godot", "env-godot", shared_caps, frozenset({"project-godot"}), frozenset({"https://github.com/example/godot"}), frozenset(), now, now + timedelta(hours=1), session_id="session-godot"),
        EnvironmentGrant("grant-roblox", "env-roblox", shared_caps, frozenset({"project-roblox"}), frozenset({"https://github.com/example/roblox"}), frozenset(), now, now + timedelta(hours=1), session_id="session-roblox"),
    )

    provider_capacity = {"groq": 0.85, "openrouter": 0.95, "nvidia": 0.20, "gemini": 0.98, "paid": 1.0}
    provider_selections = {key: 0 for key in provider_capacity}
    completed = blocked = cross_blocks = total_in = total_out = failovers = paid_selected = 0
    governor = QuotaBudgetGovernor()
    budget_router = BudgetAwareRouter(governor)

    for index in range(task_count):
        grant = grants[index % 2]
        own_project = "project-godot" if grant.environment_id == "env-godot" else "project-roblox"
        sibling_project = "project-roblox" if own_project == "project-godot" else "project-godot"
        sibling_env = "env-roblox" if grant.environment_id == "env-godot" else "env-godot"

        EnvironmentBoundary.require_project(grant, project_id=own_project, resource_environment_id=grant.environment_id, now=now)
        try:
            EnvironmentBoundary.require_project(grant, project_id=sibling_project, resource_environment_id=sibling_env, now=now)
        except EnvironmentBoundaryError:
            cross_blocks += 1
        else:
            raise AssertionError("cross-environment project access was not blocked")

        groq_health = ProviderHealth.UNAVAILABLE if index >= task_count * 3 // 10 else ProviderHealth.HEALTHY
        openrouter_quota = index < task_count * 7 // 10
        availability = (
            ProviderAvailability("groq", "groq-free", BillingClass.FREE, groq_health, groq_health == ProviderHealth.HEALTHY, provider_capacity["groq"], now, "forced stress outage" if groq_health != ProviderHealth.HEALTHY else "available"),
            ProviderAvailability("openrouter", "openrouter-free", BillingClass.FREE, ProviderHealth.HEALTHY, openrouter_quota, provider_capacity["openrouter"], now, "available" if openrouter_quota else "forced stress quota exhaustion"),
            ProviderAvailability("nvidia", "nvidia-free", BillingClass.FREE, ProviderHealth.HEALTHY, True, provider_capacity["nvidia"], now, "scarce reserve"),
            ProviderAvailability("gemini", "gemini-free", BillingClass.FREE, ProviderHealth.HEALTHY, True, provider_capacity["gemini"], now, "available"),
            ProviderAvailability("paid", "paid", BillingClass.PAID, ProviderHealth.HEALTHY, True, provider_capacity["paid"], now, "paid route must be denied"),
        )
        router = CapabilityRouter(candidates=candidates, measurements=measurements, availability=availability)
        task_class, context_tokens, output_tokens = _task_shape(index)
        session = AdaptiveRouteSession(task_class=task_class)
        request = BudgetedRoutingRequest(
            RoutingRequest(WorkerProfileKind.IMPLEMENTATION, frozenset({ModelCapability.CODING}), context_tokens),
            task_class,
            output_tokens,
        )
        try:
            decision = budget_router.route(router=router, availability=availability, request=request, session=session)
        except (BudgetExceeded, BudgetRoutingUnavailable):
            blocked += 1
            continue

        provider = decision.routing.route.provider
        provider_selections[provider] += 1
        paid_selected += int(provider == "paid")
        actual_input = max(500, int(context_tokens * 0.72))
        actual_output = max(200, int(output_tokens * 0.65))
        session.record_attempt(route_id=decision.routing.route.route_id, input_tokens=actual_input, output_tokens=actual_output, success=True, governor=governor)
        total_in += actual_input
        total_out += actual_output
        completed += 1
        provider_capacity[provider] = max(0.0, provider_capacity[provider] - (actual_input + actual_output) / 8_000_000)
        if index in {task_count * 3 // 10, task_count * 7 // 10}:
            failovers += 1

    return StressMetrics(task_count, completed, blocked, cross_blocks, total_in, total_out, provider_selections, failovers, paid_selected)


def main() -> int:
    print(json.dumps(run_stress().as_json(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
