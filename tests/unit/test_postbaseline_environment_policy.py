from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from amodex.environments.models import EnvironmentCapability, EnvironmentGrant, MemoryRecord, MemoryScope
from amodex.environments.policy import EnvironmentBoundary, EnvironmentBoundaryError


def _grant(now: datetime) -> EnvironmentGrant:
    return EnvironmentGrant(
        grant_id="grant-a",
        environment_id="env-a",
        capabilities=frozenset({EnvironmentCapability.CONTEXT_READ, EnvironmentCapability.REPOSITORY_READ, EnvironmentCapability.REPOSITORY_WRITE, EnvironmentCapability.SECRET_USE}),
        allowed_project_ids=frozenset({"project-a"}),
        allowed_repository_urls=frozenset({"https://github.com/example/a"}),
        allowed_secret_refs=frozenset({"env://ENV_A_KEY"}),
        issued_at=now,
        expires_at=now + timedelta(minutes=30),
        session_id="session-a",
    )


def _memory(*, memory_id: str, scope: MemoryScope, content: str, now: datetime, environment_id: str | None = None, session_id: str | None = None, domain_key: str | None = None) -> MemoryRecord:
    return MemoryRecord(memory_id=memory_id, scope=scope, content=content, source_ref=f"test://{memory_id}", content_sha256=hashlib.sha256(content.encode()).hexdigest(), created_at=now, environment_id=environment_id, session_id=session_id, domain_key=domain_key)


def test_environment_grant_blocks_sibling_projects_repositories_and_secrets() -> None:
    now = datetime.now(timezone.utc)
    grant = _grant(now)
    EnvironmentBoundary.require_project(grant, project_id="project-a", resource_environment_id="env-a", now=now)
    EnvironmentBoundary.require_repository(grant, "https://github.com/example/a", write=True, now=now)
    EnvironmentBoundary.require_secret(grant, "env://ENV_A_KEY", now=now)
    with pytest.raises(EnvironmentBoundaryError, match="ENVIRONMENT_BOUNDARY"):
        EnvironmentBoundary.require_project(grant, project_id="project-b", resource_environment_id="env-b", now=now)
    with pytest.raises(EnvironmentBoundaryError, match="REPOSITORY_DENIED"):
        EnvironmentBoundary.require_repository(grant, "https://github.com/example/b", now=now)
    with pytest.raises(EnvironmentBoundaryError, match="SECRET_DENIED"):
        EnvironmentBoundary.require_secret(grant, "env://ENV_B_KEY", now=now)


def test_memory_compilation_shares_owner_preferences_but_not_sibling_project_context() -> None:
    now = datetime.now(timezone.utc)
    grant = _grant(now)
    memories = (
        _memory(memory_id="owner", scope=MemoryScope.OWNER, content="prefer modular systems", now=now),
        _memory(memory_id="env-a", scope=MemoryScope.ENVIRONMENT, content="Godot project uses chunk streaming", now=now + timedelta(seconds=1), environment_id="env-a"),
        _memory(memory_id="env-b", scope=MemoryScope.ENVIRONMENT, content="Roblox project uses Open Cloud", now=now + timedelta(seconds=2), environment_id="env-b"),
        _memory(memory_id="domain-a", scope=MemoryScope.DOMAIN, content="forest maps use 64m road spacing", now=now + timedelta(seconds=3), environment_id="env-a", domain_key="maps"),
        _memory(memory_id="session-a", scope=MemoryScope.SESSION, content="currently editing the forest map", now=now + timedelta(seconds=4), environment_id="env-a", session_id="session-a"),
        _memory(memory_id="session-other", scope=MemoryScope.SESSION, content="unrelated combat session", now=now + timedelta(seconds=5), environment_id="env-a", session_id="session-other"),
    )
    visible = EnvironmentBoundary.compile_visible_memories(grant, memories, session_id="session-a", domain_key="maps", now=now)
    assert {item.memory_id for item in visible} == {"owner", "env-a", "domain-a", "session-a"}


def test_expired_grant_fails_closed() -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(EnvironmentBoundaryError, match="GRANT_EXPIRED"):
        EnvironmentBoundary.require_active(_grant(now), now=now + timedelta(hours=1))
