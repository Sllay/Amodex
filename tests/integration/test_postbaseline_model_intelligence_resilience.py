from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import psycopg
import pytest

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
)
from amodex.providers.routing import CapabilityRouter
from amodex.storage.environment import PostgresEnvironmentRepository
from amodex.storage.model_intelligence import PostgresModelIntelligenceRepository
from amodex.storage.postgres import PostgresRunRepository
from amodex.storage.product import PostgresProductRepository
from amodex.storage.routing import PostgresRoutingRepository
from amodex.storage.workspace import PostgresWorkspaceRepository
from amodex.workspace.models import ChatRole


def test_step4_benchmark_measurement_does_not_fake_tool_calling_transport() -> None:
    now = datetime.now(timezone.utc)
    candidate = ModelCandidate(
        provider="fixture",
        model="tool-declared-model",
        credential_pool_id="fixture-free",
        cost_class=BillingClass.FREE,
        capabilities=frozenset(
            {
                ModelCapability.TEXT,
                ModelCapability.CODING,
                ModelCapability.TOOL_CALLING,
            }
        ),
        context_capacity_tokens=32_000,
        lifecycle=ModelLifecycle.ELIGIBLE,
        provenance="fixture declaration",
    )
    measurement = ModelMeasurement(
        provider=candidate.provider,
        model=candidate.model,
        sample_count=6,
        structured_tool_reliability=1.0,
        coding_pass_rate=1.0,
        correction_success_rate=1.0,
        planning_schema_compliance=1.0,
        research_success_rate=1.0,
        review_detection_rate=1.0,
        vision_success_rate=None,
        latency_ms_p50=100.0,
        observed_at=now,
        source="step4-benchmark:fixture:step4-live-v1",
    )
    availability = ProviderAvailability(
        provider=candidate.provider,
        credential_pool_id=candidate.credential_pool_id,
        billing_class=BillingClass.FREE,
        health=ProviderHealth.HEALTHY,
        quota_available=True,
        free_capacity_ratio=1.0,
        observed_at=now,
        reason="fixture free capacity",
    )
    router = CapabilityRouter(
        candidates=(candidate,),
        measurements=(measurement,),
        availability=(availability,),
        minimum_samples=3,
    )

    with pytest.raises(RoutingUnavailable) as exc_info:
        router.route(RoutingRequest(profile_id=WorkerProfileKind.IMPLEMENTATION))

    assert any("tool-calling transport unmeasured" in reason for reason in exc_info.value.reasons)


def _prepare_step4_database(dsn: str) -> tuple[PostgresWorkspaceRepository, PostgresModelIntelligenceRepository]:
    runs = PostgresRunRepository(dsn)
    runs.migrate_all(Path("migrations"))
    with psycopg.connect(dsn) as conn:
        conn.execute(Path("migrations/stage4/0001_promotion.sql").read_text(encoding="utf-8"))
    routing = PostgresRoutingRepository(dsn)
    routing.migrate(Path("migrations/stage5/0001_routing_measurements.sql"))
    product = PostgresProductRepository(dsn)
    product.migrate(Path("migrations/stage6/0001_product_surfaces.sql"))
    environments = PostgresEnvironmentRepository(dsn)
    environments.migrate(Path("migrations/postbaseline/0001_environment_isolation_budget.sql"))
    with psycopg.connect(dsn) as conn:
        for path in (
            "migrations/postbaseline/0002_environment_product_runners.sql",
            "migrations/postbaseline/0003_web_workspace.sql",
            "migrations/postbaseline/0004_connectors.sql",
            "migrations/postbaseline/0005_multimodal_chat.sql",
            "migrations/postbaseline/0006_model_intelligence.sql",
        ):
            conn.execute(Path(path).read_text(encoding="utf-8"))
    return PostgresWorkspaceRepository(dsn), PostgresModelIntelligenceRepository(dsn)


def test_reconcile_marks_stale_decision_unknown_even_before_first_attempt() -> None:
    dsn = os.environ["AMODEX_TEST_DATABASE_URL"]
    workspace, model_intelligence = _prepare_step4_database(dsn)
    candidate = ModelCandidate(
        provider="fixture-reconcile",
        model="fixture-model",
        credential_pool_id="fixture-reconcile-free",
        cost_class=BillingClass.FREE,
        capabilities=frozenset({ModelCapability.TEXT, ModelCapability.PLANNING}),
        context_capacity_tokens=8_000,
        lifecycle=ModelLifecycle.ELIGIBLE,
        provenance="reconcile fixture",
    )
    model_intelligence.sync_candidates((candidate,))

    project = workspace.create_workspace(name="Step 4 reconcile")
    chat = workspace.create_chat(workspace_id=project.workspace_id, title="Crash window")
    owner = workspace.add_message(chat_id=chat.chat_id, role=ChatRole.OWNER, content="hello")
    decision_id = model_intelligence.create_decision(
        chat_id=chat.chat_id,
        owner_message_id=owner.message_id,
        profile_id=WorkerProfileKind.REPOSITORY_ANALYST.value,
        selected_route_id=candidate.route_id,
        ranked_route_ids=[candidate.route_id],
        rejected_reasons=[],
        selection_facts=["fixture"],
    )

    with psycopg.connect(dsn) as conn:
        conn.execute(
            """
            UPDATE amodex_model_route_decisions
            SET updated_at = NOW() - INTERVAL '10 minutes'
            WHERE decision_id=%s
            """,
            (decision_id,),
        )

    reconciled = model_intelligence.reconcile_stale_decisions(
        cutoff=datetime.now(timezone.utc) - timedelta(minutes=5)
    )
    decisions = model_intelligence.list_decisions(chat_id=chat.chat_id)

    assert reconciled == 1
    assert decisions[0]["decision_id"] == decision_id
    assert decisions[0]["status"] == "UNKNOWN_OUTCOME"
    assert decisions[0]["attempt_count"] == 0
    assert decisions[0]["error_code"] == "STALE_STARTED"
