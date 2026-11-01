"""Quota-aware token budgets layered on top of capability routing.

The provider router answers "which free measured route is eligible?". This module
adds "is this task worth spending that route's scarce free quota on?" and enforces
hard per-task request/token ceilings so simple work cannot silently consume a huge
share of a provider allowance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .capabilities import ProviderAvailability, RoutingDecision, RoutingRequest, RoutingUnavailable
from .routing import CapabilityRouter


class TaskBudgetClass(StrEnum):
    MICRO = "MICRO"
    ROUTINE = "ROUTINE"
    STANDARD = "STANDARD"
    COMPLEX = "COMPLEX"
    CRITICAL = "CRITICAL"


class BudgetAction(StrEnum):
    ALLOW = "ALLOW"
    PRESERVE_ROUTE = "PRESERVE_ROUTE"
    SPLIT_REQUIRED = "SPLIT_REQUIRED"
    HARD_STOP = "HARD_STOP"


@dataclass(frozen=True, slots=True)
class BudgetEnvelope:
    max_input_tokens: int
    max_output_tokens: int
    max_requests: int
    max_fallbacks: int
    reserve_floor_ratio: float

    def __post_init__(self) -> None:
        if min(self.max_input_tokens, self.max_output_tokens, self.max_requests) < 1:
            raise ValueError("token/request limits must be positive")
        if self.max_fallbacks < 0:
            raise ValueError("max_fallbacks cannot be negative")
        if not 0.0 <= self.reserve_floor_ratio <= 1.0:
            raise ValueError("reserve_floor_ratio must be between 0 and 1")


DEFAULT_BUDGETS: dict[TaskBudgetClass, BudgetEnvelope] = {
    TaskBudgetClass.MICRO: BudgetEnvelope(6_000, 2_000, 1, 1, 0.35),
    TaskBudgetClass.ROUTINE: BudgetEnvelope(12_000, 4_000, 2, 1, 0.25),
    TaskBudgetClass.STANDARD: BudgetEnvelope(24_000, 8_000, 4, 2, 0.10),
    TaskBudgetClass.COMPLEX: BudgetEnvelope(64_000, 20_000, 8, 3, 0.03),
    TaskBudgetClass.CRITICAL: BudgetEnvelope(128_000, 40_000, 12, 4, 0.0),
}


@dataclass(slots=True)
class BudgetUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def record(self, *, input_tokens: int, output_tokens: int, requests: int = 1) -> None:
        if min(input_tokens, output_tokens, requests) < 0:
            raise ValueError("usage increments cannot be negative")
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.requests += requests


@dataclass(frozen=True, slots=True)
class BudgetAssessment:
    action: BudgetAction
    reason: str
    remaining_input_tokens: int
    remaining_output_tokens: int
    remaining_requests: int


class BudgetExceeded(RuntimeError):
    def __init__(self, assessment: BudgetAssessment) -> None:
        super().__init__(assessment.reason)
        self.assessment = assessment


class BudgetRoutingUnavailable(RuntimeError):
    def __init__(self, reasons: tuple[str, ...]) -> None:
        super().__init__("no budget-safe zero-cost route")
        self.reasons = reasons


class QuotaBudgetGovernor:
    def __init__(self, envelopes: dict[TaskBudgetClass, BudgetEnvelope] | None = None) -> None:
        self._envelopes = dict(envelopes or DEFAULT_BUDGETS)

    def envelope(self, task_class: TaskBudgetClass) -> BudgetEnvelope:
        try:
            return self._envelopes[task_class]
        except KeyError as exc:
            raise ValueError(f"missing budget envelope for {task_class}") from exc

    def assess(
        self,
        *,
        task_class: TaskBudgetClass,
        usage: BudgetUsage,
        next_input_tokens: int,
        next_output_tokens: int,
        provider_free_capacity_ratio: float,
    ) -> BudgetAssessment:
        if next_input_tokens < 0 or next_output_tokens < 0:
            raise ValueError("next token estimates cannot be negative")
        if not 0.0 <= provider_free_capacity_ratio <= 1.0:
            raise ValueError("provider_free_capacity_ratio must be between 0 and 1")

        envelope = self.envelope(task_class)
        remaining_input = max(0, envelope.max_input_tokens - usage.input_tokens)
        remaining_output = max(0, envelope.max_output_tokens - usage.output_tokens)
        remaining_requests = max(0, envelope.max_requests - usage.requests)

        if remaining_requests == 0:
            return BudgetAssessment(
                BudgetAction.HARD_STOP,
                f"{task_class.value} request ceiling reached ({envelope.max_requests})",
                remaining_input,
                remaining_output,
                remaining_requests,
            )

        if next_input_tokens > remaining_input or next_output_tokens > remaining_output:
            return BudgetAssessment(
                BudgetAction.SPLIT_REQUIRED,
                (
                    f"{task_class.value} token envelope would be exceeded; "
                    "split/narrow context before another provider request"
                ),
                remaining_input,
                remaining_output,
                remaining_requests,
            )

        if provider_free_capacity_ratio < envelope.reserve_floor_ratio:
            return BudgetAssessment(
                BudgetAction.PRESERVE_ROUTE,
                (
                    f"provider free capacity {provider_free_capacity_ratio:.3f} is below "
                    f"{task_class.value} reserve floor {envelope.reserve_floor_ratio:.3f}"
                ),
                remaining_input,
                remaining_output,
                remaining_requests,
            )

        return BudgetAssessment(
            BudgetAction.ALLOW,
            "within task token/request envelope and provider reserve policy",
            remaining_input,
            remaining_output,
            remaining_requests,
        )


@dataclass(frozen=True, slots=True)
class BudgetedRoutingRequest:
    routing_request: RoutingRequest
    task_class: TaskBudgetClass
    expected_output_tokens: int

    def __post_init__(self) -> None:
        if self.expected_output_tokens < 1:
            raise ValueError("expected_output_tokens must be positive")


@dataclass(frozen=True, slots=True)
class BudgetedRoutingDecision:
    routing: RoutingDecision
    budget: BudgetAssessment


@dataclass(slots=True)
class AdaptiveRouteSession:
    task_class: TaskBudgetClass
    usage: BudgetUsage = field(default_factory=BudgetUsage)
    failed_route_ids: set[str] = field(default_factory=set)
    fallbacks: int = 0

    def record_attempt(
        self,
        *,
        route_id: str,
        input_tokens: int,
        output_tokens: int,
        success: bool,
        governor: QuotaBudgetGovernor,
    ) -> None:
        self.usage.record(input_tokens=input_tokens, output_tokens=output_tokens, requests=1)
        if success:
            return
        self.failed_route_ids.add(route_id)
        self.fallbacks += 1
        max_fallbacks = governor.envelope(self.task_class).max_fallbacks
        if self.fallbacks > max_fallbacks:
            raise BudgetExceeded(
                BudgetAssessment(
                    BudgetAction.HARD_STOP,
                    f"fallback ceiling exceeded ({max_fallbacks})",
                    max(0, governor.envelope(self.task_class).max_input_tokens - self.usage.input_tokens),
                    max(0, governor.envelope(self.task_class).max_output_tokens - self.usage.output_tokens),
                    max(0, governor.envelope(self.task_class).max_requests - self.usage.requests),
                )
            )


class BudgetAwareRouter:
    """Compose existing capability routing with quota-preservation and task ceilings."""

    def __init__(self, governor: QuotaBudgetGovernor | None = None) -> None:
        self._governor = governor or QuotaBudgetGovernor()

    def route(
        self,
        *,
        router: CapabilityRouter,
        availability: tuple[ProviderAvailability, ...],
        request: BudgetedRoutingRequest,
        session: AdaptiveRouteSession,
        excluded_route_ids: frozenset[str] = frozenset(),
    ) -> BudgetedRoutingDecision:
        excluded = set(excluded_route_ids)
        excluded.update(session.failed_route_ids)
        capacity = {
            (item.provider, item.credential_pool_id): item.free_capacity_ratio
            for item in availability
        }
        budget_rejections: list[str] = []

        while True:
            try:
                decision = router.route(
                    request.routing_request,
                    excluded_route_ids=frozenset(excluded),
                )
            except RoutingUnavailable as exc:
                reasons = tuple(budget_rejections) + tuple(exc.reasons)
                raise BudgetRoutingUnavailable(reasons) from exc

            key = (decision.route.provider, decision.route.credential_pool_id)
            if key not in capacity:
                excluded.add(decision.route.route_id)
                budget_rejections.append(
                    f"{decision.route.route_id}: budget layer missing availability"
                )
                continue

            assessment = self._governor.assess(
                task_class=request.task_class,
                usage=session.usage,
                next_input_tokens=request.routing_request.required_context_tokens,
                next_output_tokens=request.expected_output_tokens,
                provider_free_capacity_ratio=capacity[key],
            )
            if assessment.action == BudgetAction.ALLOW:
                return BudgetedRoutingDecision(decision, assessment)
            if assessment.action == BudgetAction.PRESERVE_ROUTE:
                excluded.add(decision.route.route_id)
                budget_rejections.append(
                    f"{decision.route.route_id}: {assessment.reason}"
                )
                continue
            raise BudgetExceeded(assessment)
