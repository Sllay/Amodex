"""Live vision benchmark execution without automatic lifecycle promotion.

Stage 1 measures whether a declared free route can actually inspect pixels. It
persists benchmark cases plus ``vision_success_rate`` but deliberately leaves the
route lifecycle unchanged. Promotion is a later, explicit policy step.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from statistics import median
from time import perf_counter
from typing import Any

from amodex.contracts.ids import new_uuid7
from amodex.model_intelligence.models import ModelIntelligenceError
from amodex.model_intelligence.vision_benchmarks import (
    VISION_PROMOTION_AGGREGATE,
    VISION_PROMOTION_PASS_RATE,
    VISION_SUITE_VERSION,
    score_vision_case,
    vision_benchmark_cases,
)
from amodex.providers.base import ProviderFailure
from amodex.providers.capabilities import (
    BillingClass,
    ModelCapability,
    ModelMeasurement,
    ProviderAvailability,
    ProviderHealth,
)
from amodex.providers.registry import ProviderExecutionDenied


def _latest_vision_benchmark(service: Any, route_id: str) -> dict[str, Any] | None:
    for row in service._repository.list_benchmarks(route_id=route_id, limit=50):
        if str(row.get("suite_version") or "") == VISION_SUITE_VERSION:
            return row
    return None


def _previous_measurement(service: Any, provider: str, model: str) -> ModelMeasurement | None:
    matches = [
        item
        for item in service._routing_repository.latest_measurements()
        if item.provider == provider and item.model == model
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: item.observed_at)


def _measurement_with_vision(
    *,
    previous: ModelMeasurement | None,
    provider: str,
    model: str,
    sample_count: int,
    vision_score: float,
    latency_ms_p50: float,
    observed_at: datetime,
    benchmark_id: str,
) -> ModelMeasurement:
    if previous is None:
        return ModelMeasurement(
            provider=provider,
            model=model,
            sample_count=max(sample_count, 1),
            structured_tool_reliability=0.0,
            coding_pass_rate=0.0,
            correction_success_rate=0.0,
            planning_schema_compliance=0.0,
            research_success_rate=0.0,
            review_detection_rate=0.0,
            vision_success_rate=vision_score,
            latency_ms_p50=latency_ms_p50,
            observed_at=observed_at,
            source=f"vision-benchmark:{benchmark_id}:{VISION_SUITE_VERSION}",
        )
    return replace(
        previous,
        sample_count=max(previous.sample_count, sample_count),
        vision_success_rate=vision_score,
        latency_ms_p50=latency_ms_p50,
        observed_at=observed_at,
        source=f"vision-benchmark:{benchmark_id}:{VISION_SUITE_VERSION}",
    )


async def run_vision_benchmark(
    service: Any,
    *,
    route_id: str,
    force: bool = False,
) -> dict[str, Any]:
    """Run five pixel-grounded probes and persist measurement evidence.

    ``service`` is the package's existing ``ModelIntelligenceService`` instance.
    This runner intentionally uses the same repositories and execution registry,
    so the existing FREE-route + FREE-credential-pool gate remains authoritative.
    """

    stored = service._repository.get_route(route_id)
    if stored is None:
        raise ModelIntelligenceError("ROUTE_NOT_FOUND", route_id, status_code=404)
    candidate = service._candidate_from_row(stored)
    if ModelCapability.VISION not in candidate.capabilities:
        raise ModelIntelligenceError(
            "VISION_CAPABILITY_NOT_DECLARED",
            "the selected route does not declare vision capability",
            status_code=409,
        )
    if candidate.cost_class != BillingClass.FREE:
        raise ModelIntelligenceError(
            "VISION_BENCHMARK_PAID_ROUTE_DENIED",
            "vision benchmark requires a route explicitly classified free",
            status_code=403,
        )

    previous_run = _latest_vision_benchmark(service, route_id)
    if previous_run and not force:
        started = previous_run.get("started_at")
        if isinstance(started, datetime) and service._utcnow() - started < service._benchmark_cooldown:
            raise ModelIntelligenceError(
                "BENCHMARK_COOLDOWN",
                "recent vision benchmark exists; wait for cooldown or explicitly force",
                facts=(str(previous_run.get("benchmark_id") or ""),),
            )

    try:
        client = service._execution_registry.client_for(candidate)
    except ProviderExecutionDenied as exc:
        raise ModelIntelligenceError(
            "VISION_BENCHMARK_EXECUTION_DENIED", str(exc), status_code=403
        ) from exc
    generate_with_image = getattr(client, "generate_with_image", None)
    if not callable(generate_with_image):
        raise ModelIntelligenceError(
            "VISION_TRANSPORT_NOT_IMPLEMENTED",
            f"provider adapter {candidate.provider} has no measured image transport",
            status_code=409,
        )

    cases = vision_benchmark_cases()
    benchmark_id = service._repository.start_benchmark(
        route_id=route_id,
        suite_version=VISION_SUITE_VERSION,
        provenance={
            "execution": "live-provider-image-call",
            "zero_cost_required": True,
            "suite": VISION_SUITE_VERSION,
            "fixture_source": "stdlib-deterministic-png",
            "vision_measured": True,
            "automatic_promotion": False,
            "case_count": len(cases),
        },
    )

    scores: list[float] = []
    weights: list[float] = []
    latencies: list[float] = []
    total_tokens: list[int] = []
    passed_count = 0
    terminal_failure: ProviderFailure | None = None

    for case in cases:
        request_key = f"benchmark:vision:{new_uuid7()}"
        started = perf_counter()
        try:
            response = await asyncio.to_thread(
                generate_with_image,
                model=candidate.model,
                prompt=case.prompt,
                image_data=case.image_png,
                media_type="image/png",
                request_key=request_key,
            )
        except ProviderFailure as exc:
            terminal_failure = exc
            latency_ms = (perf_counter() - started) * 1000.0
            service._repository.record_benchmark_case(
                benchmark_id=benchmark_id,
                case_id=case.case_id,
                capability=ModelCapability.VISION.value,
                score=0.0,
                passed=False,
                latency_ms=latency_ms,
                total_tokens=None,
                result_excerpt=None,
                evidence={
                    "provider_failure": exc.code,
                    "ambiguous_outcome": exc.ambiguous_outcome,
                    "retry_after_seconds": exc.retry_after_seconds,
                    "fixture_bytes": len(case.image_png),
                },
            )
            scores.append(0.0)
            weights.append(case.weight)
            latencies.append(latency_ms)
            if exc.ambiguous_outcome or service._quota_failure(exc):
                break
            continue

        latency_ms = (perf_counter() - started) * 1000.0
        score, passed, evidence = score_vision_case(case, response.text)
        service._repository.record_benchmark_case(
            benchmark_id=benchmark_id,
            case_id=case.case_id,
            capability=ModelCapability.VISION.value,
            score=score,
            passed=passed,
            latency_ms=latency_ms,
            total_tokens=response.usage.total_tokens,
            result_excerpt=response.text[:500],
            evidence={
                **evidence,
                "request_key": request_key,
                "fixture_bytes": len(case.image_png),
                "fixture_media_type": "image/png",
            },
        )
        scores.append(score)
        weights.append(case.weight)
        latencies.append(latency_ms)
        if response.usage.total_tokens is not None:
            total_tokens.append(response.usage.total_tokens)
        if passed:
            passed_count += 1

    sample_count = len(scores)
    weighted_total = sum(score * weight for score, weight in zip(scores, weights))
    aggregate = weighted_total / sum(weights) if weights else 0.0
    pass_rate = passed_count / max(sample_count, 1)
    latency_p50 = float(median(latencies)) if latencies else None
    token_sum = sum(total_tokens) if total_tokens else None

    if terminal_failure is not None and terminal_failure.ambiguous_outcome:
        status = "UNKNOWN_OUTCOME"
        error_code = terminal_failure.code
        error_message = terminal_failure.message
    elif sample_count == 0:
        status = "FAILED"
        error_code = terminal_failure.code if terminal_failure else "NO_SAMPLES"
        error_message = terminal_failure.message if terminal_failure else "no vision benchmark samples"
    else:
        status = "SUCCEEDED"
        error_code = terminal_failure.code if terminal_failure else None
        error_message = terminal_failure.message if terminal_failure else None

    finished = service._repository.finish_benchmark(
        benchmark_id=benchmark_id,
        status=status,
        sample_count=sample_count,
        passed_count=passed_count,
        aggregate_score=aggregate,
        latency_ms_p50=latency_p50,
        total_tokens=token_sum,
        progress_per_1k_tokens=None,
        error_code=error_code,
        error_message=error_message,
    )

    if status == "UNKNOWN_OUTCOME":
        return {
            **finished,
            "vision_measured": False,
            "promotion_applied": False,
            "promotion_candidate": False,
            "pass_rate": pass_rate,
        }

    observed_at = service._utcnow()
    measurement = _measurement_with_vision(
        previous=_previous_measurement(service, candidate.provider, candidate.model),
        provider=candidate.provider,
        model=candidate.model,
        sample_count=sample_count,
        vision_score=aggregate,
        latency_ms_p50=latency_p50 or 0.0,
        observed_at=observed_at,
        benchmark_id=benchmark_id,
    )
    service._routing_repository.record_measurement(measurement)

    quota_available = not (terminal_failure is not None and service._quota_failure(terminal_failure))
    healthy = quota_available and aggregate >= VISION_PROMOTION_AGGREGATE and pass_rate >= VISION_PROMOTION_PASS_RATE
    service._routing_repository.record_availability(
        ProviderAvailability(
            provider=candidate.provider,
            credential_pool_id=candidate.credential_pool_id,
            billing_class=BillingClass.FREE,
            health=ProviderHealth.HEALTHY if healthy else ProviderHealth.LIMITED,
            quota_available=quota_available,
            free_capacity_ratio=1.0 if quota_available else 0.0,
            observed_at=observed_at,
            reason=(
                f"vision benchmark {VISION_SUITE_VERSION}; aggregate={aggregate:.4f}; "
                f"pass_rate={pass_rate:.4f}; promotion_not_applied"
            ),
        )
    )

    promotion_candidate = (
        sample_count == len(cases)
        and aggregate >= VISION_PROMOTION_AGGREGATE
        and pass_rate >= VISION_PROMOTION_PASS_RATE
    )
    return {
        **finished,
        "vision_measured": True,
        "vision_success_rate": aggregate,
        "pass_rate": pass_rate,
        "required_aggregate": VISION_PROMOTION_AGGREGATE,
        "required_pass_rate": VISION_PROMOTION_PASS_RATE,
        "promotion_candidate": promotion_candidate,
        "promotion_applied": False,
        "lifecycle_unchanged": stored["lifecycle"],
    }
