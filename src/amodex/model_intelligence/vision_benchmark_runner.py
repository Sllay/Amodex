"""Live vision benchmark execution without automatic lifecycle promotion.

The benchmark measures whether a declared free route can actually inspect pixels.
Provider transport/configuration failures never count as visual mistakes: incomplete
suites are persisted as BLOCKED evidence and do not write ``vision_success_rate``.
Only a fully scored suite can create a visual measurement or promotion candidate.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timedelta
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
from amodex.providers.capabilities import BillingClass, ModelCapability, ModelMeasurement
from amodex.providers.registry import ProviderExecutionDenied


_logger = logging.getLogger("amodex.model_intelligence.vision_benchmark")
_RETRYABLE_FAILURE_CODES = frozenset(
    {
        "RATE_LIMITED",
        "CONCURRENCY_LIMITED",
        "PROVIDER_OVERLOADED",
        "PROVIDER_TIMEOUT",
        "PROVIDER_INTERNAL",
        "REQUEST_TIMEOUT",
        "NETWORK_TRANSIENT",
        "PROVIDER_OUTPUT_TRUNCATED",
    }
)
# Provider/transport failures are diagnostic failures, not visual mistakes. A case
# is scored only after a valid JSON answer reaches the pixel-grounded scorer.
_MAX_PROVIDER_ATTEMPTS_PER_CASE = 2
_MAX_INLINE_RETRY_WAIT_SECONDS = 20.0
_DEFAULT_RETRY_WAIT_SECONDS = 1.0
_INCOMPLETE_COOLDOWN = timedelta(seconds=15)


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


def _is_retryable_failure(exc: ProviderFailure) -> bool:
    return exc.code in _RETRYABLE_FAILURE_CODES


def _retry_wait_seconds(exc: ProviderFailure, *, attempt: int) -> float:
    if exc.retry_after_seconds is not None:
        return max(0.0, float(exc.retry_after_seconds))
    return min(_DEFAULT_RETRY_WAIT_SECONDS * (2 ** max(attempt - 1, 0)), _MAX_INLINE_RETRY_WAIT_SECONDS)


def _case_report(service: Any, benchmark_id: str) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    for row in service._repository.list_benchmark_cases(benchmark_id):
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        provider_failure = evidence.get("provider_failure") if isinstance(evidence, dict) else None
        explicit_scored = evidence.get("scored") if isinstance(evidence, dict) else None
        scored = bool(explicit_scored) if explicit_scored is not None else provider_failure is None
        report.append(
            {
                "case_id": str(row.get("case_id") or ""),
                "score": float(row.get("score") or 0.0),
                "passed": bool(row.get("passed")),
                "scored": scored,
                "attempts": int(evidence.get("attempts") or 1) if isinstance(evidence, dict) else 1,
                "latency_ms": float(row.get("latency_ms") or 0.0),
                "result_excerpt": row.get("result_excerpt"),
                "field_results": evidence.get("field_results") if isinstance(evidence, dict) else None,
                "provider_failure": provider_failure,
                "provider_http_status": evidence.get("provider_http_status") if isinstance(evidence, dict) else None,
                "retry_after_seconds": evidence.get("retry_after_seconds") if isinstance(evidence, dict) else None,
            }
        )
    return report


def _suite_complete(case_report: list[dict[str, Any]], expected_count: int) -> bool:
    return len(case_report) == expected_count and all(bool(item.get("scored")) for item in case_report)


def vision_benchmark_evidence(service: Any, *, benchmark_id: str) -> dict[str, Any]:
    """Read persisted visual proof without making another provider call."""

    run = next(
        (
            row
            for row in service._repository.list_benchmarks(route_id=None, limit=500)
            if str(row.get("benchmark_id") or "") == benchmark_id
        ),
        None,
    )
    if run is None or str(run.get("suite_version") or "") != VISION_SUITE_VERSION:
        raise ModelIntelligenceError(
            "VISION_BENCHMARK_NOT_FOUND",
            "visual benchmark evidence was not found",
            status_code=404,
        )

    route_id = str(run.get("route_id") or "")
    route = service._repository.get_route(route_id)
    route_enabled = bool(route and route.get("enabled", True))
    cases = _case_report(service, benchmark_id)
    expected_count = len(vision_benchmark_cases())
    complete = _suite_complete(cases, expected_count)
    scored_count = sum(1 for item in cases if item.get("scored"))
    passed_count = sum(1 for item in cases if item.get("scored") and item.get("passed"))
    aggregate = float(run.get("aggregate_score") or 0.0)
    pass_rate = passed_count / max(scored_count, 1)
    stored_status = str(run.get("status") or "")
    effective_status = stored_status
    if stored_status == "SUCCEEDED" and not complete:
        effective_status = "BLOCKED"

    measured = effective_status == "SUCCEEDED" and complete and route_enabled
    return {
        "benchmark_id": benchmark_id,
        "route_id": route_id,
        "route_enabled": route_enabled,
        "route_retired": not route_enabled,
        "suite_version": str(run.get("suite_version") or ""),
        "status": effective_status,
        "stored_status": stored_status,
        "sample_count": scored_count,
        "scored_case_count": scored_count,
        "attempted_case_count": len(cases),
        "expected_case_count": expected_count,
        "passed_count": passed_count,
        "aggregate_score": aggregate,
        "partial_score": None if measured else aggregate,
        "vision_success_rate": aggregate if measured else None,
        "vision_measured": measured,
        "suite_complete": complete,
        "pass_rate": pass_rate,
        "promotion_candidate": (
            measured
            and aggregate >= VISION_PROMOTION_AGGREGATE
            and pass_rate >= VISION_PROMOTION_PASS_RATE
        ),
        "promotion_applied": False,
        "cases": cases,
        "replayed_from_storage": True,
        "provider_call_performed": False,
    }


async def run_vision_benchmark(
    service: Any,
    *,
    route_id: str,
    force: bool = False,
) -> dict[str, Any]:
    """Run pixel-grounded probes and persist only fully valid visual measurements."""

    stored = service._repository.get_route(route_id)
    if stored is None:
        raise ModelIntelligenceError("ROUTE_NOT_FOUND", route_id, status_code=404)
    if not bool(stored.get("enabled", True)):
        raise ModelIntelligenceError(
            "VISION_ROUTE_DISABLED",
            "the selected visual route is retired or disabled",
            status_code=409,
            facts=(route_id,),
        )

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

    cases = vision_benchmark_cases()
    previous_run = _latest_vision_benchmark(service, route_id)
    if previous_run and not force:
        started = previous_run.get("started_at")
        previous_complete = (
            str(previous_run.get("status") or "") == "SUCCEEDED"
            and int(previous_run.get("sample_count") or 0) == len(cases)
        )
        cooldown = service._benchmark_cooldown if previous_complete else _INCOMPLETE_COOLDOWN
        if isinstance(started, datetime) and service._utcnow() - started < cooldown:
            raise ModelIntelligenceError(
                "BENCHMARK_COOLDOWN",
                "recent vision benchmark exists; inspect its saved proof before retrying",
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
            "shared_provider_availability_mutation": False,
            "provider_failures_are_unscored": True,
            "max_provider_attempts_per_case": _MAX_PROVIDER_ATTEMPTS_PER_CASE,
            "case_count": len(cases),
        },
    )
    _logger.info(
        "vision_benchmark_started benchmark_id=%s route_id=%s provider=%s model=%s cases=%s",
        benchmark_id,
        route_id,
        candidate.provider,
        candidate.model,
        len(cases),
    )

    scores: list[float] = []
    weights: list[float] = []
    latencies: list[float] = []
    total_tokens: list[int] = []
    passed_count = 0
    terminal_failure: ProviderFailure | None = None

    for case in cases:
        case_started = perf_counter()
        attempts = 0
        response = None
        failure: ProviderFailure | None = None

        while attempts < _MAX_PROVIDER_ATTEMPTS_PER_CASE:
            attempts += 1
            request_key = f"benchmark:vision:{new_uuid7()}"
            try:
                response = await asyncio.to_thread(
                    generate_with_image,
                    model=candidate.model,
                    prompt=case.prompt,
                    image_data=case.image_png,
                    media_type="image/png",
                    request_key=request_key,
                )
                failure = None
                break
            except ProviderFailure as exc:
                failure = exc
                if not _is_retryable_failure(exc) or attempts >= _MAX_PROVIDER_ATTEMPTS_PER_CASE:
                    break
                wait_seconds = _retry_wait_seconds(exc, attempt=attempts)
                if wait_seconds > _MAX_INLINE_RETRY_WAIT_SECONDS:
                    break
                await asyncio.sleep(wait_seconds)

        latency_ms = (perf_counter() - case_started) * 1000.0

        if response is None and failure is not None:
            terminal_failure = failure
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
                    "provider_failure": failure.code,
                    "provider_http_status": failure.http_status,
                    "scored": False,
                    "attempts": attempts,
                    "retryable": _is_retryable_failure(failure),
                    "ambiguous_outcome": failure.ambiguous_outcome,
                    "retry_after_seconds": failure.retry_after_seconds,
                    "fixture_bytes": len(case.image_png),
                },
            )
            _logger.warning(
                "vision_benchmark_case_blocked benchmark_id=%s route_id=%s case_id=%s code=%s upstream_status=%s attempts=%s",
                benchmark_id,
                route_id,
                case.case_id,
                failure.code,
                failure.http_status,
                attempts,
            )
            break

        assert response is not None
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
                "scored": True,
                "attempts": attempts,
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

    case_report = _case_report(service, benchmark_id)
    sample_count = len(scores)
    weighted_total = sum(score * weight for score, weight in zip(scores, weights))
    aggregate = weighted_total / sum(weights) if weights else 0.0
    pass_rate = passed_count / max(sample_count, 1)
    latency_p50 = float(median(latencies)) if latencies else None
    token_sum = sum(total_tokens) if total_tokens else None
    complete = _suite_complete(case_report, len(cases)) and sample_count == len(cases)

    if terminal_failure is not None and terminal_failure.ambiguous_outcome and not _is_retryable_failure(terminal_failure):
        status = "UNKNOWN_OUTCOME"
    elif terminal_failure is not None or not complete:
        status = "BLOCKED"
    elif sample_count == 0:
        status = "FAILED"
    else:
        status = "SUCCEEDED"

    error_code = terminal_failure.code if terminal_failure else (None if complete else "INCOMPLETE_SUITE")
    error_message = terminal_failure.message if terminal_failure else (None if complete else "vision suite did not complete")
    finished = service._repository.finish_benchmark(
        benchmark_id=benchmark_id,
        status=status,
        sample_count=sample_count,
        passed_count=passed_count,
        aggregate_score=aggregate if sample_count else None,
        latency_ms_p50=latency_p50,
        total_tokens=token_sum,
        progress_per_1k_tokens=None,
        error_code=error_code,
        error_message=error_message,
    )

    base_result = {
        **finished,
        "pass_rate": pass_rate,
        "partial_score": aggregate if status != "SUCCEEDED" and sample_count else None,
        "suite_complete": complete,
        "expected_case_count": len(cases),
        "attempted_case_count": len(case_report),
        "scored_case_count": sample_count,
        "cases": case_report,
        "promotion_applied": False,
        "promotion_candidate": False,
        "shared_provider_availability_mutated": False,
    }

    if status != "SUCCEEDED" or not complete:
        _logger.warning(
            "vision_benchmark_finished benchmark_id=%s route_id=%s status=%s scored=%s attempted=%s error_code=%s upstream_status=%s",
            benchmark_id,
            route_id,
            status,
            sample_count,
            len(case_report),
            error_code,
            terminal_failure.http_status if terminal_failure else None,
        )
        return {
            **base_result,
            "vision_measured": False,
            "vision_success_rate": None,
            "retryable": bool(terminal_failure and _is_retryable_failure(terminal_failure)),
            "retry_after_seconds": terminal_failure.retry_after_seconds if terminal_failure else None,
            "provider_http_status": terminal_failure.http_status if terminal_failure else None,
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

    promotion_candidate = aggregate >= VISION_PROMOTION_AGGREGATE and pass_rate >= VISION_PROMOTION_PASS_RATE
    _logger.info(
        "vision_benchmark_finished benchmark_id=%s route_id=%s status=SUCCEEDED score=%.4f pass_rate=%.4f",
        benchmark_id,
        route_id,
        aggregate,
        pass_rate,
    )
    return {
        **base_result,
        "vision_measured": True,
        "vision_success_rate": aggregate,
        "required_aggregate": VISION_PROMOTION_AGGREGATE,
        "required_pass_rate": VISION_PROMOTION_PASS_RATE,
        "promotion_candidate": promotion_candidate,
        "lifecycle_unchanged": stored["lifecycle"],
        "retryable": False,
        "retry_after_seconds": None,
        "provider_http_status": 200,
    }
