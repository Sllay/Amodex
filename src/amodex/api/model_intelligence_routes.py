"""FastAPI surface for measured Step 4 model intelligence."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from amodex.model_intelligence.models import (
    BenchmarkRunRequest,
    ChatRespondRequest,
    ModelIntelligenceError,
    ReconcileRequest,
)
from amodex.model_intelligence.service import ModelIntelligenceService
from amodex.providers.capabilities import WorkerProfileKind


def _raise(exc: ModelIntelligenceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc


def build_model_intelligence_router(service: ModelIntelligenceService) -> APIRouter:
    router = APIRouter(prefix="/v1")

    @router.get("/model-intelligence/status")
    def status() -> dict:
        return service.status()

    @router.get("/model-intelligence/routes")
    def routes() -> tuple[dict, ...]:
        return service.routes()

    @router.get("/model-intelligence/leaderboard")
    def leaderboard(
        profile: WorkerProfileKind = Query(default=WorkerProfileKind.REPOSITORY_ANALYST),
    ) -> tuple[dict, ...]:
        try:
            return tuple(item.as_dict() for item in service.leaderboard(profile))
        except ModelIntelligenceError as exc:
            _raise(exc)

    @router.get("/model-intelligence/benchmarks")
    def benchmarks(route_id: str | None = None, limit: int = Query(default=100, ge=1, le=500)) -> tuple[dict, ...]:
        return service.benchmarks(route_id=route_id, limit=limit)

    @router.post("/model-intelligence/benchmarks/run")
    async def benchmark(payload: BenchmarkRunRequest) -> dict:
        try:
            return await service.benchmark_route(route_id=payload.route_id, force=payload.force)
        except ModelIntelligenceError as exc:
            _raise(exc)

    @router.post("/model-intelligence/reconcile")
    def reconcile(payload: ReconcileRequest) -> dict[str, int]:
        return {"reconciled": service.reconcile_stale(stale_after_seconds=payload.stale_after_seconds)}

    @router.post("/chats/{chat_id}/respond")
    async def respond(chat_id: str, payload: ChatRespondRequest) -> dict:
        try:
            return await service.respond(
                chat_id=chat_id,
                owner_message_id=payload.owner_message_id,
                profile=payload.profile,
            )
        except ModelIntelligenceError as exc:
            _raise(exc)

    @router.get("/chats/{chat_id}/model-decisions")
    def chat_decisions(chat_id: str, limit: int = Query(default=100, ge=1, le=500)) -> tuple[dict, ...]:
        return service.decisions(chat_id=chat_id, limit=limit)

    return router
