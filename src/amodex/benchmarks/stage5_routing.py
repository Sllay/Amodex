"""Deterministic Stage 5 specialization/routing vertical benchmark."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone

from amodex.director.models import PlannedTask, TaskPlan, TaskState
from amodex.director.orchestrator import DagOrchestrator, fingerprint_error, validate_director_plan
from amodex.providers.capabilities import (
    BillingClass,
    ModelLifecycle,
    ModelMeasurement,
    ProviderAvailability,
    ProviderHealth,
    WorkerProfileKind,
)
from amodex.providers.catalog import stage5_declared_candidates
from amodex.providers.routing import CapabilityRouter
from amodex.runtime.specialized import SpecializedAssignment, SpecializedWorkerCoordinator


@dataclass(frozen=True, slots=True)
class Stage5BenchmarkReport:
    routes: dict[str, str]
    baseline_quality: float
    specialized_quality: float
    baseline_requests: int
    specialized_requests: int
    baseline_wall_ms: float
    specialized_wall_ms: float
    baseline_quota_requests: int
    specialized_quota_requests: int
    baseline_context_chars: int
    specialized_context_chars: int
    reviewer_channels: tuple[str, ...]
    final_states: dict[str, str]

    @property
    def context_reduction_ratio(self) -> float:
        return 1.0 - (self.specialized_context_chars / self.baseline_context_chars)


def _fixture_router() -> tuple[CapabilityRouter, dict[tuple[str, str], ModelMeasurement]]:
    observed_at = datetime(2026, 11, 1, tzinfo=timezone.utc)
    declared = stage5_declared_candidates()
    candidates = tuple(
        replace(candidate, lifecycle=ModelLifecycle.ELIGIBLE)
        for candidate in declared
        if candidate.provider in {"groq", "openrouter", "nvidia"}
    )
    measurements = (
        ModelMeasurement(
            provider="groq",
            model="openai/gpt-oss-20b",
            sample_count=40,
            structured_tool_reliability=0.96,
            coding_pass_rate=0.90,
            correction_success_rate=0.88,
            planning_schema_compliance=0.90,
            research_success_rate=0.75,
            review_detection_rate=0.70,
            vision_success_rate=None,
            latency_ms_p50=140.0,
            observed_at=observed_at,
            source="stage5 deterministic evaluation fixture",
        ),
        ModelMeasurement(
            provider="openrouter",
            model="openrouter/free",
            sample_count=40,
            structured_tool_reliability=0.86,
            coding_pass_rate=0.66,
            correction_success_rate=0.64,
            planning_schema_compliance=0.90,
            research_success_rate=0.92,
            review_detection_rate=0.74,
            vision_success_rate=None,
            latency_ms_p50=450.0,
            observed_at=observed_at,
            source="stage5 deterministic evaluation fixture",
        ),
        ModelMeasurement(
            provider="nvidia",
            model="mistralai/mistral-large-4-1t-a52b",
            sample_count=40,
            structured_tool_reliability=0.94,
            coding_pass_rate=0.86,
            correction_success_rate=0.84,
            planning_schema_compliance=0.95,
            research_success_rate=0.88,
            review_detection_rate=0.92,
            vision_success_rate=0.91,
            latency_ms_p50=650.0,
            observed_at=observed_at,
            source="stage5 deterministic evaluation fixture",
        ),
    )
    availability = (
        ProviderAvailability(
            "groq", "groq-free-primary", BillingClass.FREE, ProviderHealth.HEALTHY,
            True, 0.85, observed_at, "fixture free capacity",
        ),
        ProviderAvailability(
            "openrouter", "openrouter-free-primary", BillingClass.FREE, ProviderHealth.HEALTHY,
            True, 0.80, observed_at, "fixture free capacity",
        ),
        ProviderAvailability(
            "nvidia", "nvidia-free-primary", BillingClass.FREE, ProviderHealth.HEALTHY,
            True, 0.65, observed_at, "fixture free capacity",
        ),
    )
    router = CapabilityRouter(
        candidates=candidates,
        measurements=measurements,
        availability=availability,
        minimum_samples=10,
    )
    return router, {(item.provider, item.model): item for item in measurements}


def run_stage5_vertical_benchmark() -> Stage5BenchmarkReport:
    router, measurement_by_route = _fixture_router()
    coordinator = SpecializedWorkerCoordinator(router)
    tasks = (
        PlannedTask(
            task_id="research",
            objective="Research the accepted interface and constraints.",
            non_goals=("Do not modify repository files.",),
            acceptance_criteria=("Sources and constraints are reported.",),
        ),
        PlannedTask(
            task_id="implementation",
            objective="Implement the bounded feature from accepted research.",
            dependencies=("research",),
            write_paths=("src/feature.py",),
            non_goals=("Do not change architecture or unrelated files.",),
            acceptance_criteria=("Focused tests pass.",),
        ),
        PlannedTask(
            task_id="review",
            objective="Review contract, diff and evidence independently.",
            dependencies=("implementation",),
            non_goals=("Do not edit implementation or inspect private reasoning transcript.",),
            acceptance_criteria=("Review identifies contract or evidence defects.",),
        ),
    )
    plan = TaskPlan(
        project_id="stage5-fixture",
        base_sha="a" * 40,
        objective="Specialized research, implementation, correction and review vertical slice.",
        tasks=tasks,
    )
    validate_director_plan(plan, available_capabilities=())
    orchestrator = DagOrchestrator(plan, max_active_tasks=1, equivalent_failure_limit=2)
    available_context = {
        "contract": "C" * 1000,
        "repository-map": "M" * 3000,
        "focused-docs": "D" * 3000,
        "research-question": "Q" * 500,
        "public-sources": "P" * 4000,
        "context-capsule": "X" * 5000,
        "source": "S" * 6000,
        "tests": "T" * 4000,
        "accepted-decisions": "A" * 2000,
        "diff": "F" * 2500,
        "failing-evidence": "E" * 1500,
        "evidence": "V" * 1800,
        "acceptance-criteria": "R" * 1000,
        "implementation-transcript": "I" * 10000,
    }

    assignments: list[SpecializedAssignment] = []

    research = tasks[0]
    orchestrator.start(research.task_id)
    assignments.append(
        coordinator.assign(
            research,
            profile_id=WorkerProfileKind.RESEARCH,
            available_context=available_context,
            required_context_tokens=8_000,
        )
    )
    orchestrator.succeed(research.task_id)

    implementation = tasks[1]
    orchestrator.start(implementation.task_id)
    assignments.append(
        coordinator.assign(
            implementation,
            profile_id=WorkerProfileKind.IMPLEMENTATION,
            available_context=available_context,
            required_context_tokens=32_000,
        )
    )
    state = orchestrator.fail(
        implementation.task_id,
        fingerprint_error(
            validator_id="pytest",
            error_class="ASSERTION_FAILED",
            message="expected revision 2 but got revision 1",
            path="src/feature.py",
            failing_test="test_feature_revision",
        ),
    )
    if state != TaskState.CORRECTION_REQUIRED:
        raise AssertionError("deliberate failure did not enter correction flow")

    assignments.append(
        coordinator.assign(
            implementation,
            profile_id=WorkerProfileKind.CORRECTION,
            available_context=available_context,
            required_context_tokens=32_000,
        )
    )
    orchestrator.schedule_correction(implementation.task_id)
    orchestrator.start(implementation.task_id)
    orchestrator.succeed(implementation.task_id)

    review = tasks[2]
    orchestrator.start(review.task_id)
    reviewer_assignment = coordinator.assign(
        review,
        profile_id=WorkerProfileKind.REVIEWER,
        available_context=available_context,
        required_context_tokens=16_000,
    )
    assignments.append(reviewer_assignment)
    orchestrator.succeed(review.task_id)

    nvidia_measurement = measurement_by_route[
        ("nvidia", "mistralai/mistral-large-4-1t-a52b")
    ]
    profiles = (
        WorkerProfileKind.RESEARCH,
        WorkerProfileKind.IMPLEMENTATION,
        WorkerProfileKind.CORRECTION,
        WorkerProfileKind.REVIEWER,
    )
    baseline_quality = sum(nvidia_measurement.quality_for(profile) for profile in profiles) / len(profiles)
    baseline_requests = len(assignments)
    baseline_wall_ms = nvidia_measurement.latency_ms_p50 * baseline_requests
    baseline_context_chars = sum(len(value) for value in available_context.values()) * baseline_requests

    specialized_quality = 0.0
    specialized_wall_ms = 0.0
    routes: dict[str, str] = {}
    labels = ("research", "implementation", "correction", "review")
    for label, assignment in zip(labels, assignments, strict=True):
        route = assignment.route.route
        measurement = measurement_by_route[(route.provider, route.model)]
        specialized_quality += measurement.quality_for(assignment.profile_id)
        specialized_wall_ms += measurement.latency_ms_p50
        routes[label] = route.route_id
    specialized_quality /= len(assignments)
    specialized_context_chars = sum(item.context.selected_chars for item in assignments)

    if "implementation-transcript" in reviewer_assignment.context.channels:
        raise AssertionError("reviewer received implementation transcript")

    return Stage5BenchmarkReport(
        routes=routes,
        baseline_quality=round(baseline_quality, 6),
        specialized_quality=round(specialized_quality, 6),
        baseline_requests=baseline_requests,
        specialized_requests=len(assignments),
        baseline_wall_ms=baseline_wall_ms,
        specialized_wall_ms=specialized_wall_ms,
        baseline_quota_requests=baseline_requests,
        specialized_quota_requests=len(assignments),
        baseline_context_chars=baseline_context_chars,
        specialized_context_chars=specialized_context_chars,
        reviewer_channels=reviewer_assignment.context.channels,
        final_states={task_id: state.value for task_id, state in orchestrator.states.items()},
    )
