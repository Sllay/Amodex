"""Deterministic, fail-closed capability routing for Stage 5."""

from __future__ import annotations

from dataclasses import dataclass

from .capabilities import (
    BillingClass,
    ModelCandidate,
    ModelLifecycle,
    ModelMeasurement,
    ProviderAvailability,
    ProviderHealth,
    RoutingDecision,
    RoutingRequest,
    RoutingUnavailable,
    WorkerProfile,
    WorkerProfileKind,
    stage5_worker_profiles,
)


@dataclass(frozen=True, slots=True)
class _Eligible:
    candidate: ModelCandidate
    measurement: ModelMeasurement
    availability: ProviderAvailability
    score: float


class CapabilityRouter:
    """Select models from measured evidence, never from global popularity alone.

    Hard capability/cost/quota checks run before scoring. A high score can never
    compensate for a missing capability or paid/unknown credential class.
    """

    def __init__(
        self,
        *,
        candidates: tuple[ModelCandidate, ...],
        measurements: tuple[ModelMeasurement, ...],
        availability: tuple[ProviderAvailability, ...],
        profiles: dict[WorkerProfileKind, WorkerProfile] | None = None,
        minimum_samples: int = 1,
    ) -> None:
        if minimum_samples < 1:
            raise ValueError("minimum_samples must be >= 1")
        self._candidates = candidates
        self._profiles = profiles or stage5_worker_profiles()
        self._minimum_samples = minimum_samples
        self._measurements = self._latest_measurements(measurements)
        self._availability = {
            (item.provider, item.credential_pool_id): item for item in availability
        }

    @staticmethod
    def _latest_measurements(
        measurements: tuple[ModelMeasurement, ...],
    ) -> dict[tuple[str, str], ModelMeasurement]:
        latest: dict[tuple[str, str], ModelMeasurement] = {}
        for item in measurements:
            key = (item.provider, item.model)
            current = latest.get(key)
            if current is None or item.observed_at > current.observed_at:
                latest[key] = item
        return latest

    def route(
        self,
        request: RoutingRequest,
        *,
        excluded_route_ids: frozenset[str] = frozenset(),
    ) -> RoutingDecision:
        profile = self._profiles.get(request.profile_id)
        if profile is None:
            raise RoutingUnavailable((f"unknown worker profile {request.profile_id}",))

        eligible: list[_Eligible] = []
        rejected: list[str] = []
        hard_requirements = profile.model_requirements | request.required_capabilities

        for candidate in self._candidates:
            route_id = candidate.route_id
            if route_id in excluded_route_ids:
                rejected.append(f"{route_id}: excluded by fallback caller")
                continue
            if not candidate.enabled:
                rejected.append(f"{route_id}: disabled")
                continue
            if candidate.provider in request.provider_denylist:
                rejected.append(f"{route_id}: provider denied by task policy")
                continue
            if candidate.lifecycle not in {
                ModelLifecycle.ELIGIBLE_LIMITED,
                ModelLifecycle.ELIGIBLE,
            }:
                rejected.append(f"{route_id}: lifecycle={candidate.lifecycle.value}")
                continue
            if candidate.cost_class != BillingClass.FREE:
                rejected.append(f"{route_id}: model route is not classified free")
                continue
            missing = hard_requirements - candidate.capabilities
            if missing:
                rejected.append(
                    f"{route_id}: missing capabilities={','.join(sorted(item.value for item in missing))}"
                )
                continue
            if candidate.context_capacity_tokens < request.required_context_tokens:
                rejected.append(
                    f"{route_id}: context {candidate.context_capacity_tokens} < {request.required_context_tokens}"
                )
                continue

            observation = self._measurements.get((candidate.provider, candidate.model))
            if observation is None:
                rejected.append(f"{route_id}: no observed measurement")
                continue
            if observation.sample_count < self._minimum_samples:
                rejected.append(
                    f"{route_id}: measurement samples {observation.sample_count} < {self._minimum_samples}"
                )
                continue

            availability = self._availability.get(
                (candidate.provider, candidate.credential_pool_id)
            )
            if availability is None:
                rejected.append(f"{route_id}: credential/quota state unknown")
                continue
            if availability.billing_class != BillingClass.FREE:
                rejected.append(
                    f"{route_id}: credential billing={availability.billing_class.value}; zero-cost policy denies"
                )
                continue
            if availability.health in {
                ProviderHealth.UNAVAILABLE,
                ProviderHealth.DISABLED_POLICY,
            }:
                rejected.append(f"{route_id}: provider health={availability.health.value}")
                continue
            if not availability.quota_available:
                rejected.append(f"{route_id}: free quota unavailable ({availability.reason})")
                continue

            score = self._score(
                profile_id=request.profile_id,
                candidate=candidate,
                measurement=observation,
                availability=availability,
                required_context_tokens=request.required_context_tokens,
            )
            eligible.append(_Eligible(candidate, observation, availability, score))

        if not eligible:
            raise RoutingUnavailable(tuple(sorted(rejected)))

        ranked = sorted(
            eligible,
            key=lambda item: (-item.score, item.candidate.provider, item.candidate.model),
        )
        selected = ranked[0]
        facts = (
            f"profile={request.profile_id.value}",
            f"measurement_samples={selected.measurement.sample_count}",
            f"measurement_source={selected.measurement.source}",
            f"credential_pool={selected.candidate.credential_pool_id}",
            f"billing={selected.availability.billing_class.value}",
            f"provider_health={selected.availability.health.value}",
            f"free_capacity_ratio={selected.availability.free_capacity_ratio:.3f}",
            f"context_capacity={selected.candidate.context_capacity_tokens}",
        )
        return RoutingDecision(
            profile_id=request.profile_id,
            route=selected.candidate,
            score=selected.score,
            ranked_route_ids=tuple(item.candidate.route_id for item in ranked),
            rejected_reasons=tuple(sorted(rejected)),
            selection_facts=facts,
        )

    def fallback(
        self,
        request: RoutingRequest,
        previous: RoutingDecision,
        *,
        additional_excluded_route_ids: frozenset[str] = frozenset(),
    ) -> RoutingDecision:
        excluded = set(additional_excluded_route_ids)
        excluded.add(previous.route.route_id)
        return self.route(request, excluded_route_ids=frozenset(excluded))

    @staticmethod
    def _score(
        *,
        profile_id: WorkerProfileKind,
        candidate: ModelCandidate,
        measurement: ModelMeasurement,
        availability: ProviderAvailability,
        required_context_tokens: int,
    ) -> float:
        role_quality = measurement.quality_for(profile_id)
        reliability = measurement.structured_tool_reliability
        schema = measurement.planning_schema_compliance
        latency = 1.0 / (1.0 + (measurement.latency_ms_p50 / 1000.0))
        capacity = availability.free_capacity_ratio
        context_fit = min(
            1.0,
            candidate.context_capacity_tokens / max(required_context_tokens * 4, 1),
        )
        return round(
            role_quality * 0.40
            + reliability * 0.20
            + schema * 0.10
            + latency * 0.10
            + capacity * 0.15
            + context_fit * 0.05,
            6,
        )
