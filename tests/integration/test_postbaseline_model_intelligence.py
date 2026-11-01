from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from amodex.api.app import create_app
from amodex.api.workspace_app import create_workspace_app
from amodex.model_intelligence.models import ModelIntelligenceError
from amodex.model_intelligence.service import ModelIntelligenceService
from amodex.multimodal.clients import MultimodalClients
from amodex.multimodal.object_store import MemoryMediaStore
from amodex.providers.base import ProviderFailure
from amodex.providers.capabilities import BillingClass, WorkerProfileKind
from amodex.providers.catalog import stage5_declared_candidates
from amodex.providers.registry import ProviderExecutionDenied
from amodex.runtime.models import ProviderResponse, ProviderUsage
from amodex.storage.environment import PostgresEnvironmentRepository
from amodex.storage.environment_product import PostgresEnvironmentProductRepository
from amodex.storage.model_intelligence import PostgresModelIntelligenceRepository
from amodex.storage.multimodal import PostgresMultimodalRepository
from amodex.storage.postgres import PostgresRunRepository
from amodex.storage.product import PostgresProductRepository
from amodex.storage.routing import PostgresRoutingRepository
from amodex.storage.workspace import PostgresWorkspaceRepository


class NotReadyCapability:
    ready = False
    readiness_reason = "TEST_DISABLED"
    provider = "fixture"
    model = "fixture"
    billing_class = "free"


class NotReadyResearch:
    ready = False
    readiness_reason = "TEST_DISABLED"
    provider = "fixture"


class FakeProvider:
    def __init__(self, *, name: str) -> None:
        self.name = name
        self.chat_calls = 0
        self.fail_chat: str | None = None
        self.ambiguous_chat = False

    def generate(self, *, model: str, prompt: str, request_key: str) -> ProviderResponse:
        benchmark_markers = (
            "AMODEX_TEXT_OK",
            "AMODEX_STRUCTURED_OK",
            "PLAN:inspect>change>validate",
            "ORBIT-741",
            "def add(a,b): return a+b",
            "BUG:operator-minus-should-plus",
        )
        for marker in benchmark_markers:
            if marker in prompt:
                return ProviderResponse(
                    provider=self.name,
                    model=model,
                    text=marker,
                    usage=ProviderUsage(prompt_tokens=20, completion_tokens=5, total_tokens=25),
                )
        if "Repair the bug" in prompt:
            return ProviderResponse(
                provider=self.name,
                model=model,
                text="def add(a,b): return a+b",
                usage=ProviderUsage(prompt_tokens=20, completion_tokens=5, total_tokens=25),
            )
        self.chat_calls += 1
        if self.fail_chat:
            raise ProviderFailure(
                code=self.fail_chat,
                message=f"{self.name} synthetic failure",
                ambiguous_outcome=self.ambiguous_chat,
            )
        return ProviderResponse(
            provider=self.name,
            model=model,
            text=f"reply-from-{self.name}",
            usage=ProviderUsage(prompt_tokens=80, completion_tokens=20, total_tokens=100),
        )


class FakeRegistry:
    def __init__(self, candidates) -> None:
        self.candidates = {item.route_id: item for item in candidates}
        self.clients = {item.route_id: FakeProvider(name=item.provider) for item in candidates}

    def client_for(self, candidate):
        configured = self.candidates.get(candidate.route_id)
        if configured is None:
            raise ProviderExecutionDenied("fixture route unavailable")
        if candidate.cost_class != BillingClass.FREE:
            raise ProviderExecutionDenied("fixture zero-cost gate")
        return self.clients[candidate.route_id]


@pytest.fixture()
def state():
    dsn = os.environ["AMODEX_TEST_DATABASE_URL"]
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
    environment_product = PostgresEnvironmentProductRepository(dsn)
    workspace = PostgresWorkspaceRepository(dsn)
    multimodal = PostgresMultimodalRepository(dsn)
    with psycopg.connect(dsn) as conn:
        for path in (
            "migrations/postbaseline/0002_environment_product_runners.sql",
            "migrations/postbaseline/0003_web_workspace.sql",
            "migrations/postbaseline/0004_connectors.sql",
            "migrations/postbaseline/0005_multimodal_chat.sql",
            "migrations/postbaseline/0006_model_intelligence.sql",
        ):
            conn.execute(Path(path).read_text(encoding="utf-8"))
        conn.execute(
            """
            TRUNCATE
                amodex_workspace_projects,
                amodex_environments,
                amodex_projects,
                amodex_model_routes,
                amodex_model_measurements,
                amodex_provider_availability_observations
            CASCADE
            """
        )
    mi = PostgresModelIntelligenceRepository(dsn)
    assert workspace.schema_ready()
    assert multimodal.schema_ready()
    assert mi.schema_ready()
    candidates = tuple(
        item for item in stage5_declared_candidates() if item.provider in {"groq", "nvidia"}
    )
    registry = FakeRegistry(candidates)
    service = ModelIntelligenceService(
        repository=mi,
        routing_repository=routing,
        workspace_repository=workspace,
        execution_registry=registry,  # type: ignore[arg-type]
        candidates=candidates,
    )
    return runs, product, environments, environment_product, workspace, multimodal, routing, mi, candidates, registry, service


def _client(state):
    runs, product, environments, environment_product, workspace, multimodal, routing, mi, _, registry, service = state
    base = create_app(
        runs,
        product_repository=product,
        environment_repository=environments,
        environment_product_repository=environment_product,
    )
    disabled = MultimodalClients(
        vision=NotReadyCapability(),
        stt=NotReadyCapability(),
        tts=NotReadyCapability(),
        research=NotReadyResearch(),
    )
    app = create_workspace_app(
        base_app=base,
        workspace_repository=workspace,
        multimodal_repository=multimodal,
        media_store=MemoryMediaStore(),
        multimodal_clients=disabled,
        max_upload_bytes=1024,
        model_intelligence_repository=mi,
        routing_repository=routing,
        provider_execution_registry=registry,  # type: ignore[arg-type]
        model_intelligence_service=service,
    )
    return TestClient(app)


def _make_chat(client: TestClient):
    workspace = client.post("/v1/workspaces", json={"name": "Step 4"}).json()
    chat = client.post(
        f"/v1/workspaces/{workspace['workspace_id']}/chats",
        json={"title": "Model intelligence"},
    ).json()
    return workspace, chat


async def _benchmark_all(service, candidates):
    return [await service.benchmark_route(route_id=item.route_id) for item in candidates]


def test_step4_schema_status_and_workspace_model_ui(state) -> None:
    *_, service = state
    status = service.status()
    assert status["automatic_chat_inference"] == "BENCHMARK_REQUIRED"
    assert status["paid_fallback"] == "DENIED"
    assert status["promotion"] == "MEASURED_POLICY_ONLY"

    client = _client(state)
    with client:
        root = client.get("/")
        assert root.status_code == 200
        assert "Modelos / Benchmarks" in root.text
        assert "rotas medidas" in root.text
        models = client.get("/models")
        assert models.status_code == 200
        assert "Model Intelligence" in models.text
        assert "Rodar benchmark" in models.text


def test_real_benchmark_contract_promotes_and_routes_without_fake_vision(state) -> None:
    *_, routing, mi, candidates, registry, service = state
    import asyncio

    results = asyncio.run(_benchmark_all(service, candidates))
    assert all(row["lifecycle"] == "ELIGIBLE" for row in results)
    assert all(row["sample_count"] >= 5 for row in results)
    assert all(row["vision_measured"] is False for row in results)
    assert all(row["tool_calling_transport_measured"] is False for row in results)
    assert service.status()["automatic_chat_inference"] == "READY"

    ranking = service.leaderboard(WorkerProfileKind.REPOSITORY_ANALYST)
    assert len(ranking) == 2
    assert all("paid_fallback=denied" in row.selection_facts for row in ranking)

    with pytest.raises(ModelIntelligenceError) as exc_info:
        service.leaderboard(WorkerProfileKind.VISUAL_RUNTIME)
    assert exc_info.value.code == "NO_MEASURED_ROUTE"

    measurements = routing.latest_measurements()
    assert all(item.vision_success_rate is None for item in measurements)
    assert all(item.source.startswith("step4-benchmark:") for item in measurements)
    assert len(mi.list_lifecycle_events()) == 2
    assert sum(client.chat_calls for client in registry.clients.values()) == 0


def test_chat_primary_failure_uses_one_bounded_fallback_and_is_idempotent(state) -> None:
    *_, candidates, registry, service = state
    import asyncio

    asyncio.run(_benchmark_all(service, candidates))
    ranking = service.leaderboard(WorkerProfileKind.REPOSITORY_ANALYST)
    primary = ranking[0].route_id
    registry.clients[primary].fail_chat = "SERVER_TRANSIENT"

    client = _client(state)
    with client:
        _, chat = _make_chat(client)
        owner = client.post(
            f"/v1/chats/{chat['chat_id']}/messages",
            json={"role": "OWNER", "content": "Explique o plano"},
        ).json()
        response = client.post(
            f"/v1/chats/{chat['chat_id']}/respond",
            json={"owner_message_id": owner["message_id"], "profile": "repository-analyst"},
        )
        assert response.status_code == 200, response.text
        decision = response.json()
        assert decision["attempt_count"] == 2
        assert decision["fallback_used"] is True
        assert decision["final_route_id"] != primary

        replay = client.post(
            f"/v1/chats/{chat['chat_id']}/respond",
            json={"owner_message_id": owner["message_id"], "profile": "repository-analyst"},
        )
        assert replay.status_code == 200
        assert replay.json()["idempotent_replay"] is True
        messages = client.get(f"/v1/chats/{chat['chat_id']}/messages").json()
        assert len([item for item in messages if item["role"] == "AMODEX"]) == 1
        assert sum(fake.chat_calls for fake in registry.clients.values()) == 2


def test_ambiguous_outcome_stops_without_blind_retry_or_fallback(state) -> None:
    *_, candidates, registry, service = state
    import asyncio

    asyncio.run(_benchmark_all(service, candidates))
    for fake in registry.clients.values():
        fake.fail_chat = "REQUEST_TIMEOUT"
        fake.ambiguous_chat = True

    client = _client(state)
    with client:
        _, chat = _make_chat(client)
        owner = client.post(
            f"/v1/chats/{chat['chat_id']}/messages",
            json={"role": "OWNER", "content": "Responda isso"},
        ).json()
        response = client.post(
            f"/v1/chats/{chat['chat_id']}/respond",
            json={"owner_message_id": owner["message_id"], "profile": "repository-analyst"},
        )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "PROVIDER_OUTCOME_UNKNOWN"
        assert sum(fake.chat_calls for fake in registry.clients.values()) == 1
        decisions = client.get(f"/v1/chats/{chat['chat_id']}/model-decisions").json()
        assert decisions[0]["status"] == "UNKNOWN_OUTCOME"
        assert decisions[0]["attempt_count"] == 1


def test_persistent_failures_auto_demote_without_auto_promoting_again(state) -> None:
    *_, mi, candidates, registry, service = state
    import asyncio

    asyncio.run(_benchmark_all(service, candidates))
    for fake in registry.clients.values():
        fake.fail_chat = "SERVER_TRANSIENT"
        fake.ambiguous_chat = False

    client = _client(state)
    with client:
        _, chat = _make_chat(client)
        for index in range(3):
            owner = client.post(
                f"/v1/chats/{chat['chat_id']}/messages",
                json={"role": "OWNER", "content": f"Pergunta {index}"},
            ).json()
            response = client.post(
                f"/v1/chats/{chat['chat_id']}/respond",
                json={"owner_message_id": owner["message_id"], "profile": "repository-analyst"},
            )
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "ROUTED_RESPONSE_FAILED"

    routes = {row["route_id"]: row for row in mi.list_routes()}
    assert all(routes[item.route_id]["lifecycle"] == "ELIGIBLE_LIMITED" for item in candidates)
    events = [row for row in mi.list_lifecycle_events() if row["actor"] == "STEP4_FAILURE_POLICY"]
    assert len(events) == 2


def test_paid_route_benchmark_is_fail_closed_before_provider_call(state) -> None:
    *prefix, routing, mi, candidates, registry, _ = state
    paid = replace(candidates[0], cost_class=BillingClass.PAID)
    paid_registry = FakeRegistry((paid,))
    paid_service = ModelIntelligenceService(
        repository=mi,
        routing_repository=routing,
        workspace_repository=prefix[4],
        execution_registry=paid_registry,  # type: ignore[arg-type]
        candidates=(paid,),
    )
    import asyncio

    with pytest.raises(ModelIntelligenceError) as exc_info:
        asyncio.run(paid_service.benchmark_route(route_id=paid.route_id))
    assert exc_info.value.code == "BENCHMARK_PAID_ROUTE_DENIED"
    assert paid_registry.clients[paid.route_id].chat_calls == 0


def test_step4_schema_has_no_raw_secret_or_binary_columns() -> None:
    sql = Path("migrations/postbaseline/0006_model_intelligence.sql").read_text(encoding="utf-8").lower()
    assert "bytea" not in sql
    for name in ("api_key", "access_token", "refresh_token", "secret_value"):
        assert name not in sql
