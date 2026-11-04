from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from amodex.api.model_intelligence_routes import build_model_intelligence_router
from amodex.api.workspace_cache_routes import install_private_cache_boundary
from amodex.providers.catalog import RETIRED_DIRECT_MISTRAL_ROUTE_ID, stage5_declared_candidates
from amodex.storage.model_intelligence import PostgresModelIntelligenceRepository
from amodex.model_intelligence.service import ModelIntelligenceService


class _Repo:
    def __init__(self) -> None:
        self.rows = {
            "active": {"route_id": "active", "enabled": True},
            "retired": {"route_id": "retired", "enabled": False},
        }

    def get_route(self, route_id: str):
        return self.rows.get(route_id)


class _Service:
    def __init__(self) -> None:
        self._repository = _Repo()
        self.text_benchmark_called = False

    def status(self):
        return {"automatic_chat_inference": "READY"}

    def routes(self):
        return (
            {"route_id": "active", "enabled": True},
            {"route_id": "retired", "enabled": False},
        )

    def benchmarks(self, *, route_id=None, limit=100):
        return ()

    async def benchmark_route(self, *, route_id: str, force: bool = False):
        self.text_benchmark_called = True
        return {"route_id": route_id, "status": "SUCCEEDED"}

    def leaderboard(self, profile):
        return ()

    def reconcile_stale(self, *, stale_after_seconds: int):
        return 0

    async def respond(self, **kwargs):
        return {}

    def decisions(self, *, chat_id: str, limit: int):
        return ()


def test_routes_endpoint_never_exposes_retired_routes() -> None:
    service = _Service()
    app = FastAPI()
    app.include_router(build_model_intelligence_router(service))
    client = TestClient(app)

    response = client.get("/v1/model-intelligence/routes")
    assert response.status_code == 200
    assert [row["route_id"] for row in response.json()] == ["active"]


def test_stale_client_cannot_run_text_benchmark_on_retired_route() -> None:
    service = _Service()
    app = FastAPI()
    app.include_router(build_model_intelligence_router(service))
    client = TestClient(app)

    response = client.post(
        "/v1/model-intelligence/benchmarks/run",
        json={"route_id": "retired", "force": False},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ROUTE_DISABLED"
    assert service.text_benchmark_called is False


def test_models_dashboard_is_no_store_even_if_browser_has_service_worker() -> None:
    app = FastAPI()

    @app.get("/models", response_class=HTMLResponse)
    def models():
        return "<html>models</html>"

    install_private_cache_boundary(app)
    response = TestClient(app).get("/models")
    assert response.status_code == 200
    cache_control = response.headers["cache-control"]
    assert "no-store" in cache_control
    assert "max-age=0" in cache_control
    assert response.headers["pragma"] == "no-cache"


def test_model_dashboard_keeps_failed_routes_for_validation_but_prioritizes_measured_nvidia() -> None:
    from amodex.api.model_intelligence_web import MODEL_INTELLIGENCE_HTML

    html = MODEL_INTELLIGENCE_HTML
    assert "AMODEX_PRIMARY_ROUTE" in html
    assert "textValidated(r,runs)&&visionValidated(r,runs)" in html
    assert "Amodex · modelo principal candidato" in html
    assert "validation-routes" in html
    assert "Outros modelos · validar ou revisar" in html
    assert "Falhas anteriores" not in html  # Never misstate failed benchmarking as model removal.
    assert "Não está validado." in html
    assert "pending.forEach(r=>details.appendChild(routeCard(r,runs)))" in html


def test_model_dashboard_javascript_parses() -> None:
    import shutil
    import subprocess

    import pytest

    from amodex.api.model_intelligence_web import MODEL_INTELLIGENCE_HTML

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable in this environment")
    script = MODEL_INTELLIGENCE_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
    result = subprocess.run(
        [node, "--check"],
        input=script,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr

 
def test_retired_direct_mistral_is_hidden_even_if_legacy_database_row_is_enabled() -> None:
    service = _Service()
    service._repository.rows[RETIRED_DIRECT_MISTRAL_ROUTE_ID] = {
        "route_id": RETIRED_DIRECT_MISTRAL_ROUTE_ID,
        "enabled": True,
    }
    previous = service.routes
    service.routes = lambda: (*previous(), service._repository.rows[RETIRED_DIRECT_MISTRAL_ROUTE_ID])
    app = FastAPI()
    app.include_router(build_model_intelligence_router(service))
    client = TestClient(app)
    response = client.get("/v1/model-intelligence/routes")
    assert response.status_code == 200
    assert [r["route_id"] for r in response.json()] == ["active"]

    result = client.post(
        "/v1/model-intelligence/benchmarks/run",
        json={"route_id": RETIRED_DIRECT_MISTRAL_ROUTE_ID, "force": False},
    )
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "ROUTE_DISABLED"
    assert service.text_benchmark_called is False


def test_catalog_only_declares_nvidia_mistral_large_four() -> None:
    rows = stage5_declared_candidates()
    assert all(row.route_id != RETIRED_DIRECT_MISTRAL_ROUTE_ID for row in rows)
    assert any(row.provider == "nvidia" and row.model == "mistralai/mistral-large-4-1t-a52b" for row in rows)


def test_sync_candidates_retires_legacy_mistral_after_old_catalog_reenables_it(monkeypatch) -> None:
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.transaction.return_value.__enter__.return_value = None
    monkeypatch.setattr("amodex.storage.model_intelligence.psycopg.connect", lambda _: conn)
    repo = PostgresModelIntelligenceRepository("postgresql://fake.local/test")
    repo.sync_candidates(())
    calls = conn.execute.call_args_list
    assert len(calls) == 1
    sql, params = calls[0].args
    assert "UPDATE amodex_model_routes" in sql
    assert "enabled=FALSE" in sql
    assert "AND enabled=TRUE" in sql
    assert params == (RETIRED_DIRECT_MISTRAL_ROUTE_ID,)
    assert "DELETE" not in sql.upper()


def test_service_route_candidate_never_accepts_retired_route_even_if_marked_enabled() -> None:
    row = {
        "route_id": RETIRED_DIRECT_MISTRAL_ROUTE_ID,
        "provider": "mistral",
        "model": "mistral-large-4",
        "credential_pool_id": "mistral-free-primary",
        "cost_class": "free",
        "capabilities": ["text"],
        "context_capacity_tokens": 1000,
        "lifecycle": "ELIGIBLE",
        "provenance": "retired",
        "enabled": True,
    }
    candidate = ModelIntelligenceService._candidate_from_row(None, row)
    assert candidate.enabled is False

 
def test_chat_prefers_primary_model_even_when_agent_scores_higher() -> None:
    import asyncio
    from types import SimpleNamespace

    from amodex.model_intelligence.models import RankedRoute
    from amodex.providers.catalog import AMODEX_PRIMARY_CHAT_ROUTE_ID
    from amodex.runtime.models import ProviderResponse, ProviderUsage
    from amodex.workspace.models import ChatRole

    groq_route = "groq:openai/gpt-oss-20b:groq-free-primary"

    def ranking(route_id: str, score: float) -> RankedRoute:
        return RankedRoute(
            route_id=route_id,
            provider="nvidia" if route_id == AMODEX_PRIMARY_CHAT_ROUTE_ID else "groq",
            model="mistralai/mistral-large-4-1t-a52b" if route_id == AMODEX_PRIMARY_CHAT_ROUTE_ID else "openai/gpt-oss-20b",
            lifecycle="ELIGIBLE",
            score=score,
            base_score=score,
            progress_per_1k_tokens=1,
            selection_facts=(f"score={score}", "billing=free"),
            rejected_reasons=(),
        )

    class FakeWorkspace:
        def get_chat(self, chat_id):
            return SimpleNamespace(workspace_id="test-workspace")

        def list_messages(self, chat_id):
            return (SimpleNamespace(message_id="owner", role=ChatRole.OWNER, content="Ola Amodex"),)

        def add_message(self, **kwargs):
            assert kwargs["role"] == ChatRole.AMODEX
            assert kwargs["content"] == "Resposta de teste"
            return SimpleNamespace(message_id="assistant")

    class FakeRepo:
        selected = None
        ranked = None
        facts = None
        def get_decision_for_owner_message(self, _):
            return None

        def create_decision(self, **kwargs):
            self.selected = kwargs["selected_route_id"]
            self.ranked = kwargs["ranked_route_ids"]
            self.facts = kwargs["selection_facts"]
            return "decision-1"

        def start_attempt(self, **kwargs):
            return "attempt-1"

        def finish_attempt(self, **kwargs):
            return None

        def finish_decision(self, **kwargs):
            return {"selected_route_id": self.selected,
                    "ranked_route_ids": self.ranked,
                    "selection_facts": self.facts,
                    **kwargs}

    class FakeClient:
        def generate(self, **kwargs):
            return ProviderResponse(
                provider="nvidia",
                model=kwargs["model"],
                text="Resposta de teste",
                usage=ProviderUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            )

    repo = FakeRepo()
    service = object.__new__(ModelIntelligenceService)
    service._workspace = FakeWorkspace()
    service._repository = repo
    service._execution_registry = SimpleNamespace(client_for=lambda candidate: FakeClient())
    service._chat_prompt = lambda *args: "Converse em portugues"
    service._candidate_for_route = lambda route_id: SimpleNamespace(
        route_id=route_id,
        provider=route_id.split(":")[0],
        model="mistralai/mistral-large-4-1t-a52b" if route_id == AMODEX_PRIMARY_CHAT_ROUTE_ID else "openai/gpt-oss-20b",
    )

    agent = ranking(groq_route, 0.99)
    primary = ranking(AMODEX_PRIMARY_CHAT_ROUTE_ID, 0.90)
    service._ranked_routes = lambda **kwargs: (agent, primary)
    result = asyncio.run(service.respond(chat_id="test-chat", owner_message_id="owner", profile=None))
    assert result["status"].value == "SUCCEEDED"
    assert result["selected_route_id"] == AMODEX_PRIMARY_CHAT_ROUTE_ID
    assert result["final_route_id"] == AMODEX_PRIMARY_CHAT_ROUTE_ID
    assert result["ranked_route_ids"] == [AMODEX_PRIMARY_CHAT_ROUTE_ID, groq_route]
    assert result["attempt_count"] == 1
    assert result["fallback_used"] is False
    assert "chat_primary_policy=PRIMARY_ELIGIBLE" in result["selection_facts"]

    # If the router denied the primary, do not force a paid or unavailable route.
    service._ranked_routes = lambda **kwargs: (agent,)
    result = asyncio.run(service.respond(chat_id="test-chat", owner_message_id="owner", profile=None))
    assert result["selected_route_id"] == groq_route
    assert result["final_route_id"] == groq_route
    assert result["attempt_count"] == 1
    assert result["fallback_used"] is False  # No second network attempt; UI marks primary substitution.
    assert "chat_primary_policy=PRIMARY_UNAVAILABLE" in result["selection_facts"]


def test_provider_timeout_remains_ambiguous_and_is_logged_without_secrets() -> None:
    import inspect
    from amodex.model_intelligence.service import ModelIntelligenceService

    source = inspect.getsource(ModelIntelligenceService.respond)
    assert "chat_provider_failure route_id=%s decision_id=%s code=%s" in source
    safe_segment = source.split("chat_provider_failure route_id=", 1)[1].split("if ambiguous:", 1)[0]
    assert "exc.message" not in safe_segment
    assert "provider outcome is ambiguous; no blind retry or fallback was performed" in source


def test_routed_chat_endpoint_is_threadpooled_to_keep_health_checks_responsive() -> None:
    import inspect

    from amodex.api.model_intelligence_routes import build_model_intelligence_router

    service = _Service()
    router = build_model_intelligence_router(service)
    endpoint = next(
        route.endpoint for route in router.routes
        if route.path.endswith("/chats/{chat_id}/respond")
    )
    assert not inspect.iscoroutinefunction(endpoint)
    assert "asyncio.run" in inspect.getsource(endpoint)


def test_explicit_recovery_api_is_not_normal_send_retry() -> None:
    import inspect
    from amodex.api.model_intelligence_routes import build_model_intelligence_router
    from amodex.model_intelligence.models import ManualChatRecoveryRequest

    assert ManualChatRecoveryRequest(owner_message_id="example").owner_message_id == "example"
    router = build_model_intelligence_router(_Service())
    recover = next(
        item.endpoint for item in router.routes
        if item.path.endswith("/respond/recover")
    )
    assert not inspect.iscoroutinefunction(recover)
    source = inspect.getsource(recover)
    assert "manual_recovery=True" in source
    assert "asyncio.run(" in source
    assert "owner_message_id=payload.owner_message_id" in source


def test_manual_recovery_uses_existing_decision_and_never_retries_original_route() -> None:
    import inspect
    from amodex.model_intelligence.service import ModelIntelligenceService
    from amodex.storage.model_intelligence import PostgresModelIntelligenceRepository

    source = inspect.getsource(ModelIntelligenceService.respond)
    claim = inspect.getsource(PostgresModelIntelligenceRepository.claim_manual_recovery)
    assert "item.route_id != original_route" in source
    assert "attempts = 1" in source
    assert "ranked[:1 if manual_recovery else 2]" in source
    assert "self._repository.claim_manual_recovery(" in source
    assert "status='UNKNOWN_OUTCOME' AND attempt_count=1" in claim
    assert "AND result_message_id IS NULL" in claim
