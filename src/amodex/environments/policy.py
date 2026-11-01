"""Deterministic environment boundary enforcement."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from .models import EnvironmentCapability, EnvironmentGrant, MemoryRecord, MemoryScope


class EnvironmentBoundaryError(PermissionError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class EnvironmentBoundary:
    """Fail-closed checks used before context, repository, secret and runner access."""

    @staticmethod
    def require_active(grant: EnvironmentGrant, *, now: datetime | None = None) -> None:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if current >= grant.expires_at:
            raise EnvironmentBoundaryError("GRANT_EXPIRED", grant.grant_id)

    @classmethod
    def require_environment(
        cls,
        grant: EnvironmentGrant,
        resource_environment_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        cls.require_active(grant, now=now)
        if resource_environment_id != grant.environment_id:
            raise EnvironmentBoundaryError(
                "ENVIRONMENT_BOUNDARY",
                f"grant={grant.environment_id} resource={resource_environment_id}",
            )

    @classmethod
    def require_capability(
        cls,
        grant: EnvironmentGrant,
        capability: EnvironmentCapability,
        *,
        now: datetime | None = None,
    ) -> None:
        cls.require_active(grant, now=now)
        if capability not in grant.capabilities:
            raise EnvironmentBoundaryError("CAPABILITY_DENIED", capability.value)

    @classmethod
    def require_project(
        cls,
        grant: EnvironmentGrant,
        *,
        project_id: str,
        resource_environment_id: str,
        now: datetime | None = None,
    ) -> None:
        cls.require_environment(grant, resource_environment_id, now=now)
        if project_id not in grant.allowed_project_ids:
            raise EnvironmentBoundaryError("PROJECT_DENIED", project_id)

    @classmethod
    def require_repository(
        cls,
        grant: EnvironmentGrant,
        repository_url: str,
        *,
        write: bool = False,
        now: datetime | None = None,
    ) -> None:
        cls.require_active(grant, now=now)
        cls.require_capability(
            grant,
            EnvironmentCapability.REPOSITORY_WRITE if write else EnvironmentCapability.REPOSITORY_READ,
            now=now,
        )
        if repository_url not in grant.allowed_repository_urls:
            raise EnvironmentBoundaryError("REPOSITORY_DENIED", repository_url)

    @classmethod
    def require_secret(
        cls,
        grant: EnvironmentGrant,
        secret_ref: str,
        *,
        now: datetime | None = None,
    ) -> None:
        cls.require_capability(grant, EnvironmentCapability.SECRET_USE, now=now)
        if secret_ref not in grant.allowed_secret_refs:
            raise EnvironmentBoundaryError("SECRET_DENIED", secret_ref)

    @classmethod
    def memory_visible(
        cls,
        grant: EnvironmentGrant,
        memory: MemoryRecord,
        *,
        session_id: str | None = None,
        domain_key: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        cls.require_capability(grant, EnvironmentCapability.CONTEXT_READ, now=now)

        if memory.scope == MemoryScope.OWNER:
            return grant.allow_owner_memory

        if memory.environment_id != grant.environment_id:
            return False

        if memory.scope == MemoryScope.ENVIRONMENT:
            return True

        if memory.scope == MemoryScope.DOMAIN:
            return domain_key is not None and memory.domain_key == domain_key

        if memory.scope == MemoryScope.SESSION:
            effective_session = session_id or grant.session_id
            return effective_session is not None and memory.session_id == effective_session

        return False

    @classmethod
    def compile_visible_memories(
        cls,
        grant: EnvironmentGrant,
        memories: Iterable[MemoryRecord],
        *,
        session_id: str | None = None,
        domain_key: str | None = None,
        max_entries: int = 64,
        now: datetime | None = None,
    ) -> tuple[MemoryRecord, ...]:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        visible = [
            memory
            for memory in memories
            if cls.memory_visible(
                grant,
                memory,
                session_id=session_id,
                domain_key=domain_key,
                now=now,
            )
        ]
        visible.sort(key=lambda item: (item.created_at, item.memory_id))
        return tuple(visible[-max_entries:])
