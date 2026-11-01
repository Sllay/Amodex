from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from amodex.director.models import PlannedTask
from amodex.providers.capabilities import (
    BillingClass,
    ModelCapability,
    ModelLifecycle,
    ModelMeasurement,
    ProviderAvailability,
    ProviderHealth,
    RoutingRequest,
    RoutingUnavailable,
    WorkerProfileKind,
)
from amodex.providers.catalog import stage5_declared_candidates
from amodex.providers.routing import CapabilityRouter
from amodex.runtime.specialized import ProfileScopeViolation, SpecializedWorkerCoordinator


NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _measurement(provider: str, model: str, *, coding: float = 0.8, vision: float | None = None, latency: float = 400) -> ModelMeasurement:
    return ModelMeasurement(
        provider=provider,
        model=model,
        sample_count=20,
        structured_tool_reliability=0.9,
        coding_pass_rate=coding,
        correction_success_rate=coding,
        planning_schema_compliance=0.9,
        research_success_rate=0.85,
        review_detection_rate=0.85,
        vision_success_rate=vision,
        latency_ms_p50=latency,
        observed_at=NOW,
        source="stage5 routing test",
    )


def _availability(provider: str, pool: str, *, billing: BillingClass = BillingClass.FREE, quota: bool = True) -> ProviderAvailability:
    return ProviderAvailability(
        provider=provider,
        credential_pool_id=pool,
        billing_class=billing,
        health=ProviderHealth.HEALTHY,
        quota_available=quota,
        free_capacity_ratio=0.8 if quota else 0.0,
        observed_at=NOW,
        reason="test observation",
    )


def _eligible_candidates():
    return tuple(
        replace(item, lifecycle=ModelLifecycle.ELIGIBLE)
        for item in stage5_declared_candidates()
        if item.provider in {"groq", "nvidia"}
    )


def test_hard_cost_gate_beats_a_higher_model_score() -> None:
    groq, nvidia = _eligible_candidates()
    router = CapabilityRouter(
        candidates=(groq, nvidia),
        measurements=(
            _measurement("groq", groq.model, coding=0.82, latency=180),
            _measurement("nvidia", nvidia.model, coding=0.99, vision=0.99, latency=100),
        ),
        availability=(
            _availability("groq", groq.credential_pool_id),
            _availability("nvidia", nvidia.credential_pool_id, billing=BillingClass.PAID),
        ),
        minimum_samples=10,
    )

    decision = router.route(
        RoutingRequest(
            profile_id=WorkerProfileKind.IMPLEMENTATION,
            required_context_tokens=20_000,
        )
    )

    assert decision.route.provider == "groq"
    assert any("credential billing=paid" in reason for reason in decision.rejected_reasons)


def test_fallback_preserves_required_capabilities() -> None:
    groq, nvidia = _eligible_candidates()
    router = CapabilityRouter(
        candidates=(groq, nvidia),
        measurements=(
            _measurement("groq", groq.model, coding=0.95, latency=120),
            _measurement("nvidia", nvidia.model, coding=0.90, vision=0.93, latency=500),
        ),
        availability=(
            _availability("groq", groq.credential_pool_id),
            _availability("nvidia", nvidia.credential_pool_id),
        ),
        minimum_samples=10,
    )
    request = RoutingRequest(
        profile_id=WorkerProfileKind.IMPLEMENTATION,
        required_context_tokens=30_000,
    )
    first = router.route(request)
    assert first.route.provider == "groq"
    fallback = router.fallback(request, first)
    assert fallback.route.provider == "nvidia"

    vision_request = RoutingRequest(
        profile_id=WorkerProfileKind.VISUAL_RUNTIME,
        required_capabilities=frozenset({ModelCapability.VISION}),
        required_context_tokens=30_000,
    )
    vision = router.route(vision_request)
    assert vision.route.provider == "nvidia"
    with pytest.raises(RoutingUnavailable):
        router.fallback(vision_request, vision)


def test_all_free_quota_unavailable_waits_instead_of_using_paid_or_unknown() -> None:
    groq, nvidia = _eligible_candidates()
    router = CapabilityRouter(
        candidates=(groq, nvidia),
        measurements=(
            _measurement("groq", groq.model),
            _measurement("nvidia", nvidia.model, vision=0.9),
        ),
        availability=(
            _availability("groq", groq.credential_pool_id, quota=False),
            _availability("nvidia", nvidia.credential_pool_id, billing=BillingClass.UNKNOWN),
        ),
    )
    with pytest.raises(RoutingUnavailable) as raised:
        router.route(RoutingRequest(profile_id=WorkerProfileKind.RESEARCH))
    joined = " | ".join(raised.value.reasons)
    assert "free quota unavailable" in joined
    assert "billing=unknown" in joined


def test_declared_catalog_is_not_eligible_without_benchmark_promotion() -> None:
    candidate = next(item for item in stage5_declared_candidates() if item.provider == "nvidia")
    router = CapabilityRouter(
        candidates=(candidate,),
        measurements=(_measurement("nvidia", candidate.model, vision=0.9),),
        availability=(_availability("nvidia", candidate.credential_pool_id),),
    )
    with pytest.raises(RoutingUnavailable) as raised:
        router.route(RoutingRequest(profile_id=WorkerProfileKind.REVIEWER))
    assert any("BENCHMARK_PENDING" in reason for reason in raised.value.reasons)


def test_read_only_profile_cannot_inherit_write_authority() -> None:
    nvidia = replace(
        next(item for item in stage5_declared_candidates() if item.provider == "nvidia"),
        lifecycle=ModelLifecycle.ELIGIBLE,
    )
    router = CapabilityRouter(
        candidates=(nvidia,),
        measurements=(_measurement("nvidia", nvidia.model, vision=0.9),),
        availability=(_availability("nvidia", nvidia.credential_pool_id),),
    )
    coordinator = SpecializedWorkerCoordinator(router)
    task = PlannedTask(
        task_id="review-write",
        objective="Review the change.",
        write_paths=("src/feature.py",),
        non_goals=("Do not expand scope.",),
        acceptance_criteria=("Review completed.",),
    )
    with pytest.raises(ProfileScopeViolation):
        coordinator.assign(
            task,
            profile_id=WorkerProfileKind.REVIEWER,
            available_context={"contract": "rules", "diff": "patch"},
        )
