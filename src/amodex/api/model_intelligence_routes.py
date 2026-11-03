"""FastAPI surface for measured Step 4 model intelligence."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from amodex.model_intelligence.models import (
    BenchmarkRunRequest,
    ChatRespondRequest,
    ModelIntelligenceError,
    ReconcileRequest,
)
from amodex.model_intelligence.service import ModelIntelligenceService
from amodex.model_intelligence.vision_benchmark_runner import (
    run_vision_benchmark,
    vision_benchmark_evidence,
)
from amodex.providers.capabilities import WorkerProfileKind


_logger = logging.getLogger("amodex.api.model_intelligence")


def _raise(exc: ModelIntelligenceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc


def _require_enabled_route(service: ModelIntelligenceService, route_id: str) -> None:
    row = service._repository.get_route(route_id)
    if row is None:
        raise ModelIntelligenceError("ROUTE_NOT_FOUND", route_id, status_code=404)
    if not bool(row.get("enabled", True)):
        raise ModelIntelligenceError(
            "ROUTE_DISABLED",
            "the selected model route is retired or disabled",
            status_code=409,
            facts=(route_id,),
        )


def build_model_intelligence_router(service: ModelIntelligenceService) -> APIRouter:
    router = APIRouter(prefix="/v1")

    @router.get("/model-intelligence/status")
    def status() -> dict:
        return service.status()

    @router.get("/model-intelligence/routes")
    def routes() -> tuple[dict, ...]:
        # Active UI/API discovery must never depend on a particular frontend
        # version to hide retired routes. Historical evidence remains available
        # through the explicit benchmark evidence endpoint.
        return tuple(row for row in service.routes() if bool(row.get("enabled", True)))

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

    @router.get("/model-intelligence/benchmarks/vision/{benchmark_id}/evidence")
    def saved_vision_benchmark_evidence(benchmark_id: str) -> dict:
        try:
            return vision_benchmark_evidence(service, benchmark_id=benchmark_id)
        except ModelIntelligenceError as exc:
            _raise(exc)

    @router.post("/model-intelligence/benchmarks/run")
    async def benchmark(payload: BenchmarkRunRequest) -> dict:
        try:
            _require_enabled_route(service, payload.route_id)
            return await service.benchmark_route(route_id=payload.route_id, force=payload.force)
        except ModelIntelligenceError as exc:
            _raise(exc)

    @router.post("/model-intelligence/benchmarks/vision/run")
    async def vision_benchmark(payload: BenchmarkRunRequest) -> dict:
        """Explicit owner-triggered pixel-grounded benchmark; never auto-promotes."""
        try:
            _require_enabled_route(service, payload.route_id)
            result = await run_vision_benchmark(
                service,
                route_id=payload.route_id,
                force=payload.force,
            )
            _logger.info(
                "vision_benchmark_http_completed route_id=%s benchmark_status=%s provider_http_status=%s vision_measured=%s",
                payload.route_id,
                result.get("status"),
                result.get("provider_http_status"),
                result.get("vision_measured"),
            )
            return result
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
