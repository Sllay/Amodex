"""Measured model promotion, leaderboard routing and trusted chat inference."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from statistics import median
from time import perf_counter
from typing import Any

from amodex.contracts.ids import new_uuid7
from amodex.model_intelligence.benchmarks import SUITE_VERSION, benchmark_cases, score_case
from amodex.model_intelligence.models import (
    AttemptStatus,
    DecisionStatus,
    ModelIntelligenceError,
    RankedRoute,
)
from amodex.providers.base import ProviderFailure
from amodex.providers.capabilities import (
    BillingClass,
    ModelCandidate,
    ModelCapability,
    ModelLifecycle,
    ModelMeasurement,
    ProviderAvailability,
    ProviderHealth,
    RoutingRequest,
    RoutingUnavailable,
    WorkerProfileKind,
    stage5_worker_profiles,
)
from amodex.providers.catalog import stage5_declared_candidates
from amodex.providers.registry import ProviderExecutionDenied, ProviderExecutionRegistry
from amodex.providers.routing import CapabilityRouter
from amodex.storage.model_intelligence import PostgresModelIntelligenceRepository
from amodex.storage.routing import PostgresRoutingRepository
from amodex.storage.workspace import PostgresWorkspaceRepository
from amodex.workspace.models import ChatRole


_SENSITIVE_PROFILES = frozenset(
    {
        WorkerProfileKind.IMPLEMENTATION,
        WorkerProfileKind.CORRECTION,
        WorkerProfileKind.REVIEWER,
        WorkerProfileKind.INTEGRATION,
        WorkerProfileKind.GODOT,
        WorkerProfileKind.VISUAL_RUNTIME,
    }
)


class ModelIntelligenceService:
    def __init__(
        self,
        *,
        repository: PostgresModelIntelligenceRepository,
        routing_repository: PostgresRoutingRepository,
        workspace_repository: PostgresWorkspaceRepository,
        execution_registry: ProviderExecutionRegistry,
        candidates: tuple[ModelCandidate, ...] | None = None,
        measurement_max_age: timedelta = timedelta(days=7),
        availability_max_age: timedelta = timedelta(hours=6),
        benchmark_cooldown: timedelta = timedelta(minutes=30),
    ) -> None:
        self._repository = repository
        self._routing_repository = routing_repository
        self._workspace = workspace_repository
        self._execution_registry = execution_registry
        self._declared_candidates = candidates or stage5_declared_candidates()
        self._profiles = stage5_worker_profiles()
        self._measurement_max_age = measurement_max_age
        self._availability_max_age = availability_max_age
        self._benchmark_cooldown = benchmark_cooldown
        self._repository.sync_candidates(self._declared_candidates)

    def status(self) -> dict[str, Any]:
        routes = self._repository.list_routes()
        eligible = sum(
            1
            for row in routes
            if row["lifecycle"]
            in {ModelLifecycle.ELIGIBLE.value, ModelLifecycle.ELIGIBLE_LIMITED.value}
        )
        try:
            ranking = self.leaderboard(WorkerProfileKind.REPOSITORY_ANALYST)
        except ModelIntelligenceError:
            ranking = ()
        return {
            "step": 4,
            "model_intelligence": "READY",
            "automatic_chat_inference": "READY" if ranking else "BENCHMARK_REQUIRED",
            "declared_routes": len(routes),
            "lifecycle_eligible_routes": eligible,
            "routable_chat_routes": len(ranking),
            "benchmark_suite": SUITE_VERSION,
            "benchmark_execution": "EXPLICIT_OWNER_ACTION",
            "paid_fallback": "DENIED",
            "unknown_billing": "DENIED",
            "promotion": "MEASURED_POLICY_ONLY",
            "visual_routing": "REQUIRES_REAL_VISION_MEASUREMENT",
            "execution_authority": "ENVIRONMENT_ONLY",
        }

    def routes(self) -> tuple[dict[str, Any], ...]:
        latest: dict[str, dict[str, Any]] = {}
        for row in self._repository.list_benchmarks(limit=500):
            latest.setdefault(str(row["route_id"]), row)
        result: list[dict[str, Any]] = []
        for row in self._repository.list_routes():
            item = dict(row)
            item["latest_benchmark"] = latest.get(str(row["route_id"]))
            result.append(item)
        return tuple(result)

    def benchmarks(self, *, route_id: str | None = None, limit: int = 100) -> tuple[dict[str, Any], ...]:
        return self._repository.list_benchmarks(route_id=route_id, limit=limit)

    async def benchmark_route(self, *, route_id: str, force: bool = False) -> dict[str, Any]:
        stored = self._repository.get_route(route_id)
        if stored is None:
            raise ModelIntelligenceError("ROUTE_NOT_FOUND", route_id, status_code=404)
        candidate = self._candidate_from_row(stored)
        if candidate.cost_class != BillingClass.FREE:
            raise ModelIntelligenceError(
                "BENCHMARK_PAID_ROUTE_DENIED",
                "benchmark execution requires a route explicitly classified free",
                status_code=403,
            )
        previous = self._repository.latest_benchmark(route_id)
        if previous and not force:
            started = previous["started_at"]
            if isinstance(started, datetime) and self._utcnow() - started < self._benchmark_cooldown:
                raise ModelIntelligenceError(
                    "BENCHMARK_COOLDOWN",
                    "recent benchmark exists; wait for cooldown or explicitly force",
                    facts=(str(previous["benchmark_id"]),),
                )

        try:
            client = self._execution_registry.client_for(candidate)
        except ProviderExecutionDenied as exc:
            raise ModelIntelligenceError(
                "BENCHMARK_EXECUTION_DENIED", str(exc), status_code=403
            ) from exc

        cases = benchmark_cases(candidate)
        if not cases:
            raise ModelIntelligenceError(
                "BENCHMARK_NO_MEASURABLE_CAPABILITIES",
                "the current benchmark transport cannot measure this route",
            )

        benchmark_id = self._repository.start_benchmark(
            route_id=route_id,
            suite_version=SUITE_VERSION,
            provenance={
                "execution": "live-provider-call",
                "zero_cost_required": True,
                "suite": SUITE_VERSION,
                "vision_measured": False,
                "tool_calling_transport_measured": False,
                "long_context_measured": False,
            },
        )
        scores: list[float] = []
        latencies: list[float] = []
        tokens: list[int] = []
        passed_count = 0
        terminal_failure: ProviderFailure | None = None

        for case in cases:
            request_key = new_uuid7()
            started = perf_counter()
            try:
                response = await asyncio.to_thread(
                    client.generate,
                    model=candidate.model,
                    prompt=case.prompt,
                    request_key=request_key,
                )
            except ProviderFailure as exc:
                terminal_failure = exc
                latency_ms = (perf_counter() - started) * 1000.0
                self._repository.record_benchmark_case(
                    benchmark_id=benchmark_id,
                    case_id=case.case_id,
                    capability=case.capability.value,
                    score=0.0,
                    passed=False,
                    latency_ms=latency_ms,
                    total_tokens=None,
                    result_excerpt=None,
                    evidence={
                        "provider_failure": exc.code,
                        "ambiguous_outcome": exc.ambiguous_outcome,
                        "retry_after_seconds": exc.retry_after_seconds,
                    },
                )
                scores.append(0.0)
                latencies.append(latency_ms)
                if exc.ambiguous_outcome or self._quota_failure(exc):
                    break
                continue

            latency_ms = (perf_counter() - started) * 1000.0
            score, passed, evidence = score_case(case, response.text)
            total_tokens = response.usage.total_tokens
            self._repository.record_benchmark_case(
                benchmark_id=benchmark_id,
                case_id=case.case_id,
                capability=case.capability.value,
                score=score,
                passed=passed,
                latency_ms=latency_ms,
                total_tokens=total_tokens,
                result_excerpt=response.text[:500],
                evidence={**evidence, "request_key": request_key},
            )
            scores.append(score)
            latencies.append(latency_ms)
            if total_tokens is not None:
                tokens.append(total_tokens)
            if passed:
                passed_count += 1

        sample_count = len(scores)
        aggregate = self._weighted_aggregate(cases[:sample_count], scores)
        latency_p50 = float(median(latencies)) if latencies else None
        total_tokens_sum = sum(tokens) if tokens else None
        progress = (
            (passed_count / max(sample_count, 1)) * 1000.0 / total_tokens_sum
            if total_tokens_sum and total_tokens_sum > 0
            else 0.0
        )

        if terminal_failure is not None and terminal_failure.ambiguous_outcome:
            status = "UNKNOWN_OUTCOME"
            error_code = terminal_failure.code
            error_message = terminal_failure.message
        elif sample_count == 0:
            status = "FAILED"
            error_code = terminal_failure.code if terminal_failure else "NO_SAMPLES"
            error_message = terminal_failure.message if terminal_failure else "no benchmark samples"
        else:
            status = "SUCCEEDED"
            error_code = terminal_failure.code if terminal_failure else None
            error_message = terminal_failure.message if terminal_failure else None

        finished = self._repository.finish_benchmark(
            benchmark_id=benchmark_id,
            status=status,
            sample_count=sample_count,
            passed_count=passed_count,
            aggregate_score=aggregate,
            latency_ms_p50=latency_p50,
            total_tokens=total_tokens_sum,
            progress_per_1k_tokens=progress,
            error_code=error_code,
            error_message=error_message,
        )

        if status == "UNKNOWN_OUTCOME":
            self._routing_repository.record_availability(
                ProviderAvailability(
                    provider=candidate.provider,
                    credential_pool_id=candidate.credential_pool_id,
                    billing_class=BillingClass.FREE,
                    health=ProviderHealth.DEGRADED,
                    quota_available=True,
                    free_capacity_ratio=0.10,
                    observed_at=self._utcnow(),
                    reason="benchmark provider outcome ambiguous",
                )
            )
            return {**finished, "lifecycle": stored["lifecycle"]}

        measurement = self._measurement_from_benchmark(
            candidate=candidate,
            sample_count=sample_count,
            case_scores=self._repository.list_benchmark_cases(benchmark_id),
            latency_ms_p50=latency_p50 or 0.0,
            benchmark_id=benchmark_id,
        )
        self._routing_repository.record_measurement(measurement)
        quota_available = not (terminal_failure is not None and self._quota_failure(terminal_failure))
        health = ProviderHealth.HEALTHY if quota_available and aggregate >= 0.75 else ProviderHealth.LIMITED
        self._routing_repository.record_availability(
            ProviderAvailability(
                provider=candidate.provider,
                credential_pool_id=candidate.credential_pool_id,
                billing_class=BillingClass.FREE,
                health=health,
                quota_available=quota_available,
                free_capacity_ratio=1.0 if quota_available else 0.0,
                observed_at=self._utcnow(),
                reason=("Step 4 benchmark evidence" if quota_available else "free quota exhausted during benchmark"),
            )
        )
        target = self._lifecycle_for(
            sample_count=sample_count,
            passed_count=passed_count,
            aggregate=aggregate,
            minimum_case_score=min(scores) if scores else 0.0,
        )
        updated = self._repository.set_lifecycle(
            route_id=route_id,
            lifecycle=target,
            reason=(
                f"suite={SUITE_VERSION}; samples={sample_count}; passed={passed_count}; "
                f"aggregate={aggregate:.4f}"
            ),
            actor="STEP4_BENCHMARK_POLICY",
            evidence_ref=benchmark_id,
        )
        return {
            **finished,
            "lifecycle": updated["lifecycle"],
            "vision_measured": False,
            "tool_calling_transport_measured": False,
            "long_context_measured": False,
        }

    def leaderboard(self, profile: WorkerProfileKind) -> tuple[RankedRoute, ...]:
        ranked = self._ranked_routes(profile=profile, required_context_tokens=1)
        if not ranked:
            raise ModelIntelligenceError(
                "NO_MEASURED_ROUTE",
                f"no measured zero-cost route is eligible for {profile.value}",
                status_code=503,
            )
        return ranked

    async def respond(
        self,
        *,
        chat_id: str,
        owner_message_id: str | None,
        profile: WorkerProfileKind | None,
    ) -> dict[str, Any]:
        chat = self._workspace.get_chat(chat_id)
        if chat is None:
            raise ModelIntelligenceError("CHAT_NOT_FOUND", chat_id, status_code=404)
        messages = self._workspace.list_messages(chat_id=chat_id)
        owner = None
        if owner_message_id is not None:
            owner = next((item for item in messages if item.message_id == owner_message_id), None)
        else:
            owner = next((item for item in reversed(messages) if item.role == ChatRole.OWNER), None)
        if owner is None:
            raise ModelIntelligenceError("OWNER_MESSAGE_NOT_FOUND", "owner message not found", status_code=404)
        if owner.role != ChatRole.OWNER:
            raise ModelIntelligenceError("OWNER_MESSAGE_REQUIRED", "source message must have OWNER role")

        existing = self._repository.get_decision_for_owner_message(owner.message_id)
        if existing:
            if existing["status"] == DecisionStatus.SUCCEEDED.value:
                return {**existing, "idempotent_replay": True}
            raise ModelIntelligenceError(
                "DECISION_ALREADY_EXISTS",
                f"message already has decision status={existing['status']}",
                facts=(str(existing["decision_id"]),),
            )

        chosen_profile = profile or self._infer_profile(owner.content)
        prompt = self._chat_prompt(chat.workspace_id, messages, owner.message_id)
        required_context_tokens = max(1, len(prompt) // 4)
        ranked = self._ranked_routes(
            profile=chosen_profile,
            required_context_tokens=required_context_tokens,
        )
        if not ranked:
            raise ModelIntelligenceError(
                "NO_MEASURED_ROUTE",
                f"no measured zero-cost route is eligible for {chosen_profile.value}",
                status_code=503,
            )

        decision_id = self._repository.create_decision(
            chat_id=chat_id,
            owner_message_id=owner.message_id,
            profile_id=chosen_profile.value,
            selected_route_id=ranked[0].route_id,
            ranked_route_ids=[item.route_id for item in ranked],
            rejected_reasons=list(ranked[0].rejected_reasons),
            selection_facts=list(ranked[0].selection_facts),
        )

        attempts = 0
        last_code = "ROUTE_FAILED"
        last_message = "all bounded routes failed"
        for item in ranked[:2]:
            attempts += 1
            candidate = self._candidate_for_route(item.route_id)
            request_key = new_uuid7()
            attempt_id = self._repository.start_attempt(
                decision_id=decision_id,
                route_id=item.route_id,
                attempt_number=attempts,
                request_key=request_key,
            )
            started = perf_counter()
            try:
                client = self._execution_registry.client_for(candidate)
                response = await asyncio.to_thread(
                    client.generate,
                    model=candidate.model,
                    prompt=prompt,
                    request_key=request_key,
                )
            except ProviderFailure as exc:
                latency_ms = (perf_counter() - started) * 1000.0
                ambiguous = exc.ambiguous_outcome
                self._repository.finish_attempt(
                    attempt_id=attempt_id,
                    status=(AttemptStatus.UNKNOWN_OUTCOME if ambiguous else AttemptStatus.FAILED),
                    latency_ms=latency_ms,
                    total_tokens=None,
                    error_code=exc.code,
                    error_message=exc.message,
                )
                last_code, last_message = exc.code, exc.message
                if ambiguous:
                    decision = self._repository.finish_decision(
                        decision_id=decision_id,
                        status=DecisionStatus.UNKNOWN_OUTCOME,
                        final_route_id=item.route_id,
                        attempt_count=attempts,
                        fallback_used=attempts > 1,
                        result_message_id=None,
                        error_code=exc.code,
                        error_message=exc.message,
                    )
                    raise ModelIntelligenceError(
                        "PROVIDER_OUTCOME_UNKNOWN",
                        "provider outcome is ambiguous; no blind retry or fallback was performed",
                        status_code=503,
                        facts=(str(decision["decision_id"]), item.route_id),
                    ) from exc
                self._observe_failure(candidate=candidate, provider_failure=exc)
                self._maybe_demote_after_failures(item.route_id)
                continue
            except ProviderExecutionDenied as exc:
                latency_ms = (perf_counter() - started) * 1000.0
                self._repository.finish_attempt(
                    attempt_id=attempt_id,
                    status=AttemptStatus.FAILED,
                    latency_ms=latency_ms,
                    total_tokens=None,
                    error_code="EXECUTION_DENIED",
                    error_message=str(exc),
                )
                last_code, last_message = "EXECUTION_DENIED", str(exc)
                self._maybe_demote_after_failures(item.route_id)
                continue

            latency_ms = (perf_counter() - started) * 1000.0
            self._repository.finish_attempt(
                attempt_id=attempt_id,
                status=AttemptStatus.SUCCEEDED,
                latency_ms=latency_ms,
                total_tokens=response.usage.total_tokens,
                error_code=None,
                error_message=None,
            )
            assistant = self._workspace.add_message(
                chat_id=chat_id,
                role=ChatRole.AMODEX,
                content=response.text,
            )
            decision = self._repository.finish_decision(
                decision_id=decision_id,
                status=DecisionStatus.SUCCEEDED,
                final_route_id=item.route_id,
                attempt_count=attempts,
                fallback_used=attempts > 1,
                result_message_id=assistant.message_id,
                error_code=None,
                error_message=None,
            )
            return {**decision, "idempotent_replay": False}

        decision = self._repository.finish_decision(
            decision_id=decision_id,
            status=DecisionStatus.FAILED,
            final_route_id=None,
            attempt_count=attempts,
            fallback_used=attempts > 1,
            result_message_id=None,
            error_code=last_code,
            error_message=last_message,
        )
        raise ModelIntelligenceError(
            "ROUTED_RESPONSE_FAILED",
            "all bounded measured routes failed",
            status_code=503,
            facts=(str(decision["decision_id"]),),
        )

    def decisions(self, *, chat_id: str, limit: int = 100) -> tuple[dict[str, Any], ...]:
        return self._repository.list_decisions(chat_id=chat_id, limit=limit)

    def reconcile_stale(self, *, stale_after_seconds: int = 300) -> int:
        cutoff = self._utcnow() - timedelta(seconds=stale_after_seconds)
        return self._repository.reconcile_stale_decisions(cutoff=cutoff)

    def _ranked_routes(
        self,
        *,
        profile: WorkerProfileKind,
        required_context_tokens: int,
    ) -> tuple[RankedRoute, ...]:
        now = self._utcnow()
        measurements = tuple(
            item
            for item in self._routing_repository.latest_measurements()
            if now - item.observed_at <= self._measurement_max_age
        )
        availability = tuple(
            item
            for item in self._routing_repository.latest_availability()
            if now - item.observed_at <= self._availability_max_age
        )
        measurement_map = {(item.provider, item.model): item for item in measurements}
        candidates: list[ModelCandidate] = []
        for row in self._repository.list_routes():
            candidate = self._candidate_from_row(row)
            measurement = measurement_map.get((candidate.provider, candidate.model))
            if profile in _SENSITIVE_PROFILES and candidate.lifecycle != ModelLifecycle.ELIGIBLE:
                continue
            if measurement is None:
                continue
            if profile == WorkerProfileKind.VISUAL_RUNTIME and measurement.vision_success_rate is None:
                continue
            quality = measurement.quality_for(profile)
            floor = 0.78 if profile in _SENSITIVE_PROFILES else 0.62
            if quality < floor:
                continue
            if profile in {
                WorkerProfileKind.IMPLEMENTATION,
                WorkerProfileKind.CORRECTION,
                WorkerProfileKind.INTEGRATION,
                WorkerProfileKind.GODOT,
            } and measurement.structured_tool_reliability < 0.70:
                continue
            candidates.append(candidate)

        if not candidates:
            return ()
        router = CapabilityRouter(
            candidates=tuple(candidates),
            measurements=measurements,
            availability=availability,
            profiles=self._profiles,
            minimum_samples=3,
        )
        request = RoutingRequest(
            profile_id=profile,
            required_context_tokens=required_context_tokens,
        )
        ranked: list[RankedRoute] = []
        excluded: set[str] = set()
        rejection_union: set[str] = set()
        while len(excluded) < len(candidates):
            try:
                decision = router.route(request, excluded_route_ids=frozenset(excluded))
            except RoutingUnavailable as exc:
                rejection_union.update(exc.reasons)
                break
            route_id = decision.route.route_id
            benchmark = self._repository.latest_benchmark(route_id)
            progress = float(benchmark.get("progress_per_1k_tokens") or 0.0) if benchmark else 0.0
            efficiency_bonus = min(progress / 5.0, 0.03)
            score = round(decision.score + efficiency_bonus, 6)
            facts = decision.selection_facts + (
                f"lifecycle={decision.route.lifecycle.value}",
                f"progress_per_1k_tokens={progress:.6f}",
                "paid_fallback=denied",
            )
            ranked.append(
                RankedRoute(
                    route_id=route_id,
                    provider=decision.route.provider,
                    model=decision.route.model,
                    lifecycle=decision.route.lifecycle.value,
                    score=score,
                    base_score=decision.score,
                    progress_per_1k_tokens=progress,
                    selection_facts=facts,
                    rejected_reasons=decision.rejected_reasons,
                )
            )
            rejection_union.update(decision.rejected_reasons)
            excluded.add(route_id)
        return tuple(sorted(ranked, key=lambda item: (-item.score, item.provider, item.model)))

    def _candidate_from_row(self, row: dict[str, Any]) -> ModelCandidate:
        capabilities = frozenset(ModelCapability(item) for item in row["capabilities"])
        return ModelCandidate(
            provider=str(row["provider"]),
            model=str(row["model"]),
            credential_pool_id=str(row["credential_pool_id"]),
            cost_class=BillingClass(str(row["cost_class"])),
            capabilities=capabilities,
            context_capacity_tokens=int(row["context_capacity_tokens"]),
            lifecycle=ModelLifecycle(str(row["lifecycle"])),
            provenance=str(row["provenance"]),
            enabled=bool(row["enabled"]),
        )

    def _candidate_for_route(self, route_id: str) -> ModelCandidate:
        row = self._repository.get_route(route_id)
        if row is None:
            raise ModelIntelligenceError("ROUTE_NOT_FOUND", route_id, status_code=404)
        return self._candidate_from_row(row)

    def _measurement_from_benchmark(
        self,
        *,
        candidate: ModelCandidate,
        sample_count: int,
        case_scores: tuple[dict[str, Any], ...],
        latency_ms_p50: float,
        benchmark_id: str,
    ) -> ModelMeasurement:
        by_capability: dict[str, list[float]] = {}
        for row in case_scores:
            by_capability.setdefault(str(row["capability"]), []).append(float(row["score"]))

        def value(capability: ModelCapability, default: float = 0.0) -> float:
            scores = by_capability.get(capability.value)
            return sum(scores) / len(scores) if scores else default

        structured = value(ModelCapability.STRUCTURED_OUTPUT, value(ModelCapability.TEXT))
        planning = value(ModelCapability.PLANNING, value(ModelCapability.TEXT))
        coding = value(ModelCapability.CODING, planning)
        correction = value(ModelCapability.CORRECTION, coding)
        research = value(ModelCapability.RESEARCH, planning)
        review = value(ModelCapability.REVIEW, planning)
        return ModelMeasurement(
            provider=candidate.provider,
            model=candidate.model,
            sample_count=max(sample_count, 1),
            structured_tool_reliability=structured,
            coding_pass_rate=coding,
            correction_success_rate=correction,
            planning_schema_compliance=planning,
            research_success_rate=research,
            review_detection_rate=review,
            vision_success_rate=None,
            latency_ms_p50=latency_ms_p50,
            observed_at=self._utcnow(),
            source=f"step4-benchmark:{benchmark_id}:{SUITE_VERSION}",
        )

    def _observe_failure(self, *, candidate: ModelCandidate, provider_failure: ProviderFailure) -> None:
        health = ProviderHealth.LIMITED if self._quota_failure(provider_failure) else ProviderHealth.DEGRADED
        quota = not self._quota_failure(provider_failure)
        self._routing_repository.record_availability(
            ProviderAvailability(
                provider=candidate.provider,
                credential_pool_id=candidate.credential_pool_id,
                billing_class=BillingClass.FREE,
                health=health,
                quota_available=quota,
                free_capacity_ratio=0.0 if not quota else 0.15,
                observed_at=self._utcnow(),
                reason=f"chat failure {provider_failure.code}",
            )
        )

    def _maybe_demote_after_failures(self, route_id: str) -> None:
        failures = self._repository.recent_failed_attempts(
            route_id=route_id,
            since=self._utcnow() - timedelta(hours=1),
        )
        if failures < 3:
            return
        row = self._repository.get_route(route_id)
        if row is None:
            return
        current = ModelLifecycle(str(row["lifecycle"]))
        if current == ModelLifecycle.ELIGIBLE:
            target = ModelLifecycle.ELIGIBLE_LIMITED
        elif current == ModelLifecycle.ELIGIBLE_LIMITED:
            target = ModelLifecycle.BENCHMARK_PENDING
        else:
            return
        self._repository.set_lifecycle(
            route_id=route_id,
            lifecycle=target,
            reason=f"{failures} failed routed attempts within 1h",
            actor="STEP4_FAILURE_POLICY",
            evidence_ref=None,
        )

    def _chat_prompt(self, workspace_id: str, messages: tuple[Any, ...], owner_message_id: str) -> str:
        recent = messages[-12:]
        approved = [
            item
            for item in self._workspace.list_memories(workspace_id=workspace_id)
            if item.status.value == "APPROVED"
        ][:12]
        memory_text = "\n".join(f"- {item.content}" for item in approved)
        chat_text = "\n".join(f"{item.role.value}: {item.content}" for item in recent)
        return (
            "You are Amodex inside a project workspace. Answer the owner's latest message directly and "
            "usefully. Do not claim that repository/code execution happened merely because you can chat. "
            "Execution requires an authorized Environment.\n\n"
            f"APPROVED PROJECT MEMORY:\n{memory_text or '(none)'}\n\n"
            f"RECENT CHAT:\n{chat_text}\n\n"
            f"SOURCE_OWNER_MESSAGE_ID:{owner_message_id}"
        )

    @staticmethod
    def _infer_profile(text: str) -> WorkerProfileKind:
        value = text.casefold()
        if any(word in value for word in ("corrija", "corrigir", "fix", "erro", "bug")):
            return WorkerProfileKind.CORRECTION
        if any(word in value for word in ("implemente", "implementar", "código", "codigo", "script", "program")):
            return WorkerProfileKind.IMPLEMENTATION
        if any(word in value for word in ("pesquise", "pesquisar", "research", "fontes", "fonte")):
            return WorkerProfileKind.RESEARCH
        if any(word in value for word in ("revise", "review", "audite", "auditar")):
            return WorkerProfileKind.REVIEWER
        if any(word in value for word in ("integre", "migre", "migração", "migracao")):
            return WorkerProfileKind.INTEGRATION
        return WorkerProfileKind.REPOSITORY_ANALYST

    @staticmethod
    def _lifecycle_for(
        *,
        sample_count: int,
        passed_count: int,
        aggregate: float,
        minimum_case_score: float,
    ) -> ModelLifecycle:
        pass_rate = passed_count / max(sample_count, 1)
        if sample_count >= 5 and aggregate >= 0.86 and pass_rate >= 0.80 and minimum_case_score >= 0.70:
            return ModelLifecycle.ELIGIBLE
        if sample_count >= 3 and aggregate >= 0.75 and pass_rate >= 0.67:
            return ModelLifecycle.ELIGIBLE_LIMITED
        return ModelLifecycle.BENCHMARK_PENDING

    @staticmethod
    def _weighted_aggregate(cases: tuple[Any, ...], scores: list[float]) -> float:
        if not scores:
            return 0.0
        weights = [float(cases[index].weight) for index in range(min(len(cases), len(scores)))]
        if not weights:
            return 0.0
        numerator = sum(scores[index] * weights[index] for index in range(len(weights)))
        return numerator / sum(weights)

    @staticmethod
    def _quota_failure(exc: ProviderFailure) -> bool:
        code = exc.code.upper()
        return "QUOTA" in code or "RATE" in code or "LIMIT" in code

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)
