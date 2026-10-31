"""Fail-closed zero-cost provider admission for Stage 1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AdmissionStatus(StrEnum):
    ADMITTED = "ADMITTED"
    WAITING_PROVIDER = "WAITING_PROVIDER"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class ProviderRoute:
    provider: str
    model: str
    cost_class: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    status: AdmissionStatus
    route: ProviderRoute | None
    reason: str


class ProviderGovernor:
    """Minimal Stage 1 governor. Only explicitly configured free routes may be admitted."""

    def __init__(self, routes: tuple[ProviderRoute, ...]) -> None:
        if not routes:
            raise ValueError("at least one provider route is required")
        self._routes = routes

    @classmethod
    def stage1_default(cls) -> "ProviderGovernor":
        return cls((ProviderRoute(provider="gemini", model="gemini-3.5-flash-lite", cost_class="free"),))

    def admit(self, *, free_quota_available: bool = True) -> AdmissionDecision:
        for route in self._routes:
            if not route.enabled or route.cost_class != "free":
                continue
            if not free_quota_available:
                return AdmissionDecision(AdmissionStatus.WAITING_PROVIDER, None, "free quota unavailable; paid fallback forbidden")
            return AdmissionDecision(AdmissionStatus.ADMITTED, route, "eligible zero-cost route")
        return AdmissionDecision(AdmissionStatus.BLOCKED, None, "no eligible zero-cost route configured")
