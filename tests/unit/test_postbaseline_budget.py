from __future__ import annotations

from datetime import datetime, timezone

import pytest

from amodex.providers.budget import (
    AdaptiveRouteSession,
    BudgetAction,
    BudgetAwareRouter,
    BudgetExceeded,
    BudgetedRoutingRequest,
    BudgetUsage,
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


def _candidate(provider: str, quality: float, *, paid: bool = False) -> tuple[ModelCandidate, ModelMeasurement]:
    now = datetime.now(timezone.utc)
    model = f"{provider}-model"
    candidate = ModelCandidate(
        provider=provider,
        model=model,
        credential_pool_id=f"{provider}-pool",
        cost_class=BillingClass.PAID if paid else BillingClass.FREE,
        capabilities=frozenset({ModelCapability.TEXT, ModelCapability.CODING, ModelCapability.TOOL_CALLING}),
        context_capacity_tokens=128_000,
        lifecycle=ModelLifecycle.ELIGIBLE,
        provenance="test",
    )
    measurement = ModelMeasurement(
        provider=provider,
        model=model,
        sample_count=10,
        structured_tool_reliability=quality,
        coding_pass_rate=quality,
        correction_success_rate=quality,
        planning_schema_compliance=quality,
        research_success_rate=quality,
        review_detection_rate=quality,
        vision_success_rate=None,
        latency_ms_p50=1000,
        observed_at=now,
        source="test",
    )
    return candidate, measurement


def _availability(provider: str, ratio: float, *, paid: bool = False) -> ProviderAvailability:
    return ProviderAvailability(
        provider=provider,
        credential_pool_id=f"{provider}-pool",
        billing_class=BillingClass.PAID if paid else BillingClass.FREE,
        health=ProviderHealth.HEALTHY,
        quota_available=True,
        free_capacity_ratio=ratio,
        observed_at=datetime.now(timezone.utc),
        reason="test",
    )


def test_micro_task_preserves_scarce_high_quality_route_and_uses_abundant_route() -> None:
    premium, premium_measurement = _candidate("premium-free", 0.98)
    abundant, abundant_measurement = _candidate("abundant-free", 0.72)
    paid, paid_measurement = _candidate("paid", 1.0, paid=True)
    availability = (_availability("premium-free", 0.10), _availability("abundant-free", 0.90), _availability("paid", 1.0, paid=True))
    router = CapabilityRouter(candidates=(premium, abundant, paid), measurements=(premium_measurement, abundant_measurement, paid_measurement), availability=availability)
    request = BudgetedRoutingRequest(
        routing_request=RoutingRequest(profile_id=WorkerProfileKind.IMPLEMENTATION, required_context_tokens=2_000),
        task_class=TaskBudgetClass.MICRO,
        expected_output_tokens=500,
    )
    decision = BudgetAwareRouter().route(router=router, availability=availability, request=request, session=AdaptiveRouteSession(task_class=TaskBudgetClass.MICRO))
    assert decision.routing.route.provider == "abundant-free"
    assert decision.budget.action == BudgetAction.ALLOW
    assert decision.routing.route.provider != "paid"


def test_critical_task_can_use_scarce_route_when_it_is_best_measured_option() -> None:
    premium, premium_measurement = _candidate("premium-free", 0.98)
    abundant, abundant_measurement = _candidate("abundant-free", 0.55)
    availability = (_availability("premium-free", 0.05), _availability("abundant-free", 0.90))
    router = CapabilityRouter(candidates=(premium, abundant), measurements=(premium_measurement, abundant_measurement), availability=availability)
    request = BudgetedRoutingRequest(
        routing_request=RoutingRequest(profile_id=WorkerProfileKind.IMPLEMENTATION, required_context_tokens=60_000),
        task_class=TaskBudgetClass.CRITICAL,
        expected_output_tokens=12_000,
    )
    decision = BudgetAwareRouter().route(router=router, availability=availability, request=request, session=AdaptiveRouteSession(task_class=TaskBudgetClass.CRITICAL))
    assert decision.routing.route.provider == "premium-free"


def test_simple_work_is_hard_capped_instead_of_burning_hundreds_of_thousands_of_tokens() -> None:
    assessment = QuotaBudgetGovernor().assess(
        task_class=TaskBudgetClass.MICRO,
        usage=BudgetUsage(),
        next_input_tokens=20_000,
        next_output_tokens=1_000,
        provider_free_capacity_ratio=1.0,
    )
    assert assessment.action == BudgetAction.SPLIT_REQUIRED
    assert assessment.remaining_input_tokens == 6_000


def test_failed_routes_are_not_repeated_and_fallbacks_have_a_ceiling() -> None:
    governor = QuotaBudgetGovernor()
    session = AdaptiveRouteSession(task_class=TaskBudgetClass.ROUTINE)
    session.record_attempt(route_id="a:model:pool", input_tokens=1_000, output_tokens=100, success=False, governor=governor)
    assert "a:model:pool" in session.failed_route_ids
    with pytest.raises(BudgetExceeded, match="fallback ceiling"):
        session.record_attempt(route_id="b:model:pool", input_tokens=1_000, output_tokens=100, success=False, governor=governor)
