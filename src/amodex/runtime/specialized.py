"""Stage 5 specialized worker assignment boundary.

This module narrows context and selects a measured model route. It never changes
DAG state, acquires writer ownership, or grants promotion/cost authority.
"""

from __future__ import annotations

from dataclasses import dataclass

from amodex.director.models import PlannedTask
from amodex.providers.capabilities import (
    ModelCapability,
    RoutingDecision,
    RoutingRequest,
    WorkerProfile,
    WorkerProfileKind,
    stage5_worker_profiles,
)
from amodex.providers.routing import CapabilityRouter


class ProfileScopeViolation(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RoleContext:
    profile_id: WorkerProfileKind
    channels: tuple[str, ...]
    content: dict[str, str]
    selected_chars: int
    available_chars: int

    @property
    def reduction_ratio(self) -> float:
        if self.available_chars <= 0:
            return 0.0
        return 1.0 - (self.selected_chars / self.available_chars)


@dataclass(frozen=True, slots=True)
class SpecializedAssignment:
    task_id: str
    profile_id: WorkerProfileKind
    context: RoleContext
    route: RoutingDecision
    standing_authority_granted: bool = False


class SpecializedWorkerCoordinator:
    def __init__(
        self,
        router: CapabilityRouter,
        *,
        profiles: dict[WorkerProfileKind, WorkerProfile] | None = None,
    ) -> None:
        self.router = router
        self.profiles = profiles or stage5_worker_profiles()

    def compile_role_context(
        self,
        profile_id: WorkerProfileKind,
        available_context: dict[str, str],
    ) -> RoleContext:
        profile = self.profiles[profile_id]
        selected = {
            channel: text
            for channel, text in available_context.items()
            if channel in profile.allowed_context_channels
        }
        ordered = dict(sorted(selected.items()))
        return RoleContext(
            profile_id=profile_id,
            channels=tuple(ordered),
            content=ordered,
            selected_chars=sum(len(value) for value in ordered.values()),
            available_chars=sum(len(value) for value in available_context.values()),
        )

    def assign(
        self,
        task: PlannedTask,
        *,
        profile_id: WorkerProfileKind,
        available_context: dict[str, str],
        required_model_capabilities: frozenset[ModelCapability] = frozenset(),
        required_context_tokens: int = 1,
        provider_denylist: frozenset[str] = frozenset(),
        excluded_route_ids: frozenset[str] = frozenset(),
    ) -> SpecializedAssignment:
        profile = self.profiles[profile_id]
        declared_profile = getattr(task, "worker_profile", None)
        if declared_profile and declared_profile != profile_id.value:
            raise ProfileScopeViolation(
                f"{task.task_id}: Director assigned profile {declared_profile}, not {profile_id.value}"
            )
        if profile.read_only and task.write_paths:
            raise ProfileScopeViolation(
                f"{task.task_id}: read-only profile {profile_id.value} cannot execute write scope"
            )
        context = self.compile_role_context(profile_id, available_context)
        if "contract" in available_context and "contract" not in context.content:
            raise ProfileScopeViolation(f"{profile_id.value}: project contract was not disclosed")
        decision = self.router.route(
            RoutingRequest(
                profile_id=profile_id,
                required_capabilities=required_model_capabilities,
                required_context_tokens=required_context_tokens,
                provider_denylist=provider_denylist,
            ),
            excluded_route_ids=excluded_route_ids,
        )
        return SpecializedAssignment(
            task_id=task.task_id,
            profile_id=profile_id,
            context=context,
            route=decision,
        )
