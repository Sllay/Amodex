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
