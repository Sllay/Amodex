"""Durable environment/session/memory contracts.

An Environment is the strongest project-context boundary below the owner account.
Directors and workers receive one EnvironmentGrant and must not discover resources
from sibling environments. Owner-level memory is the only intentionally shared
memory layer and remains read-only to ordinary worker grants.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class EnvironmentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class MemoryScope(StrEnum):
    OWNER = "OWNER"
    ENVIRONMENT = "ENVIRONMENT"
    DOMAIN = "DOMAIN"
    SESSION = "SESSION"


class EnvironmentCapability(StrEnum):
    CONTEXT_READ = "context.read"
    REPOSITORY_READ = "repository.read"
    REPOSITORY_WRITE = "repository.write"
    PROVIDER_USE = "provider.use"
    RUNNER_USE = "runner.use"
    SECRET_USE = "secret.use"
    VALIDATE = "validate"
    PROMOTION_REQUEST = "promotion.request"


@dataclass(frozen=True, slots=True)
class EnvironmentRecord:
    environment_id: str
    name: str
    engine: str | None
    status: EnvironmentStatus
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.environment_id.strip():
            raise ValueError("environment_id must be non-empty")
        if not self.name.strip():
            raise ValueError("name must be non-empty")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: str
    environment_id: str
    title: str
    domain_key: str | None
    status: SessionStatus
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.session_id.strip() or not self.environment_id.strip():
            raise ValueError("session_id/environment_id must be non-empty")
        if not self.title.strip():
            raise ValueError("title must be non-empty")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    memory_id: str
    scope: MemoryScope
    content: str
    source_ref: str
    content_sha256: str
    created_at: datetime
    environment_id: str | None = None
    session_id: str | None = None
    domain_key: str | None = None

    def __post_init__(self) -> None:
        if not self.memory_id.strip():
            raise ValueError("memory_id must be non-empty")
        if not self.content.strip():
            raise ValueError("memory content must be non-empty")
        if not self.source_ref.strip():
            raise ValueError("source_ref must be non-empty")
        if len(self.content_sha256) != 64:
            raise ValueError("content_sha256 must be a 64-character digest")
        _require_aware(self.created_at, "created_at")

        if self.scope == MemoryScope.OWNER:
            if self.environment_id is not None or self.session_id is not None or self.domain_key is not None:
                raise ValueError("OWNER memory cannot be environment/session/domain bound")
        elif self.scope == MemoryScope.ENVIRONMENT:
            if self.environment_id is None or self.session_id is not None or self.domain_key is not None:
                raise ValueError("ENVIRONMENT memory requires only environment_id")
        elif self.scope == MemoryScope.DOMAIN:
            if self.environment_id is None or not self.domain_key or self.session_id is not None:
                raise ValueError("DOMAIN memory requires environment_id + domain_key")
        elif self.scope == MemoryScope.SESSION:
            if self.environment_id is None or self.session_id is None:
                raise ValueError("SESSION memory requires environment_id + session_id")


@dataclass(frozen=True, slots=True)
class EnvironmentGrant:
    grant_id: str
    environment_id: str
    capabilities: frozenset[EnvironmentCapability]
    allowed_project_ids: frozenset[str]
    allowed_repository_urls: frozenset[str]
    allowed_secret_refs: frozenset[str]
    issued_at: datetime
    expires_at: datetime
    run_id: str | None = None
    session_id: str | None = None
    allow_owner_memory: bool = True

    def __post_init__(self) -> None:
        if not self.grant_id.strip() or not self.environment_id.strip():
            raise ValueError("grant_id/environment_id must be non-empty")
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("grant expires_at must be later than issued_at")
