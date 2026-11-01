from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

from amodex.environments.models import MemoryScope
from amodex.storage.environment import EnvironmentStateError, PostgresEnvironmentRepository
from amodex.storage.postgres import PostgresRunRepository
from amodex.storage.product import PostgresProductRepository
from amodex.storage.routing import PostgresRoutingRepository


@pytest.fixture()
def environment_state() -> tuple[PostgresRunRepository, PostgresProductRepository, PostgresEnvironmentRepository]:
    dsn = os.environ["AMODEX_TEST_DATABASE_URL"]
    runs = PostgresRunRepository(dsn)
    runs.migrate_all(Path("migrations"))
    with psycopg.connect(dsn) as conn:
        conn.execute(Path("migrations/stage4/0001_promotion.sql").read_text(encoding="utf-8"))
    PostgresRoutingRepository(dsn).migrate(Path("migrations/stage5/0001_routing_measurements.sql"))
    product = PostgresProductRepository(dsn)
    product.migrate(Path("migrations/stage6/0001_product_surfaces.sql"))
    environments = PostgresEnvironmentRepository(dsn)
    environments.migrate(Path("migrations/postbaseline/0001_environment_isolation_budget.sql"))

    with psycopg.connect(dsn) as conn:
        conn.execute(
            """
            TRUNCATE
                amodex_environment_usage_events,
                amodex_environment_budget_policies,
                amodex_memories,
                amodex_session_runs,
                amodex_sessions,
                amodex_environment_projects,
                amodex_environments,
                amodex_audit_events,
                amodex_checkpoint_views,
                amodex_evidence_views,
                amodex_task_views,
                amodex_runners,
                amodex_provider_connections,
                amodex_approvals,
                amodex_run_controls,
                amodex_promotions,
                amodex_candidates,
                amodex_provider_availability_observations,
                amodex_model_measurements,
                amodex_run_events,
                amodex_runs,
                amodex_projects
            CASCADE
            """
        )
    return runs, product, environments


def test_projects_runs_sessions_memories_and_usage_cannot_cross_environment(environment_state) -> None:
    runs, product, environments = environment_state
    assert environments.schema_ready()

    godot = environments.create_environment(name="Black Island", engine="Godot")
    roblox = environments.create_environment(name="Roblox RPG", engine="Roblox")
    godot_project = product.create_project(repo_url="https://github.com/example/godot", name="Godot Game")
    roblox_project = product.create_project(repo_url="https://github.com/example/roblox", name="Roblox Game")
    environments.bind_project(environment_id=godot.environment_id, project_id=godot_project.project_id)
    environments.bind_project(environment_id=roblox.environment_id, project_id=roblox_project.project_id)

    with pytest.raises(EnvironmentStateError, match="PROJECT_ALREADY_BOUND"):
        environments.bind_project(environment_id=roblox.environment_id, project_id=godot_project.project_id)

    godot_session = environments.create_session(environment_id=godot.environment_id, title="Fishing system", domain_key="gameplay")
    roblox_session = environments.create_session(environment_id=roblox.environment_id, title="Capital map", domain_key="maps")
    godot_run = runs.create_run(prompt="implement fishing", project_id=godot_project.project_id)
    environments.bind_run(environment_id=godot.environment_id, session_id=godot_session.session_id, run_id=godot_run.run_id)
    assert environments.run_environment(godot_run.run_id) == godot.environment_id

    with pytest.raises(EnvironmentStateError, match="SESSION_ENVIRONMENT_MISMATCH"):
        environments.bind_run(environment_id=godot.environment_id, session_id=roblox_session.session_id, run_id=godot_run.run_id)

    environments.put_memory(scope=MemoryScope.OWNER, content="BK prefers modular systems and small validated checkpoints.", source_ref="owner://preference")
    environments.put_memory(scope=MemoryScope.ENVIRONMENT, environment_id=godot.environment_id, content="Godot project uses mobile-first chunk streaming.", source_ref="repo://godot/architecture")
    environments.put_memory(scope=MemoryScope.ENVIRONMENT, environment_id=roblox.environment_id, content="Roblox project uses Open Cloud test places.", source_ref="repo://roblox/architecture")
    environments.put_memory(scope=MemoryScope.SESSION, environment_id=godot.environment_id, session_id=godot_session.session_id, content="Fishing task is currently editing only gameplay/fishing paths.", source_ref="session://fishing")

    visible = environments.list_visible_memories(environment_id=godot.environment_id, session_id=godot_session.session_id, domain_key="gameplay")
    content = {item.content for item in visible}
    assert "BK prefers modular systems and small validated checkpoints." in content
    assert "Godot project uses mobile-first chunk streaming." in content
    assert "Fishing task is currently editing only gameplay/fishing paths." in content
    assert "Roblox project uses Open Cloud test places." not in content

    environments.record_usage(environment_id=godot.environment_id, session_id=godot_session.session_id, run_id=godot_run.run_id, task_id="fishing-implementation", provider="groq", model="example", input_tokens=2_000, output_tokens=500, requests=1, outcome="PASS")
    assert environments.usage_summary(environment_id=godot.environment_id) == {"input_tokens": 2_000, "output_tokens": 500, "requests": 1}
    assert environments.usage_summary(environment_id=roblox.environment_id) == {"input_tokens": 0, "output_tokens": 0, "requests": 0}

    with pytest.raises(EnvironmentStateError, match="USAGE_ENVIRONMENT_MISMATCH"):
        environments.record_usage(environment_id=roblox.environment_id, run_id=godot_run.run_id, provider="nvidia", model="example", input_tokens=1, output_tokens=1, requests=1, outcome="DENIED")


def test_environment_budget_policy_is_durable_and_independent(environment_state) -> None:
    _, _, environments = environment_state
    a = environments.create_environment(name="A", engine="Godot")
    b = environments.create_environment(name="B", engine="Roblox")
    environments.upsert_budget_policy(environment_id=a.environment_id, policy={"routine": {"max_input_tokens": 12000}, "reserve": 0.25})
    assert environments.get_budget_policy(environment_id=a.environment_id) == {"reserve": 0.25, "routine": {"max_input_tokens": 12000}}
    assert environments.get_budget_policy(environment_id=b.environment_id) is None
