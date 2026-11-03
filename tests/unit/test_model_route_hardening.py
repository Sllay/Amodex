from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from amodex.api.model_intelligence_routes import build_model_intelligence_router
from amodex.api.workspace_cache_routes import install_private_cache_boundary


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
