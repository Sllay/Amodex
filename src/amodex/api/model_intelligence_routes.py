"""FastAPI surface for measured Step 4 model intelligence."""

from __future__ import annotations

import asyncio
import json
import logging
from time import monotonic
import os
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from amodex.model_intelligence.models import (
    BenchmarkRunRequest,
    ChatRespondRequest,
    ManualChatRecoveryRequest,
    ModelIntelligenceError,
    ReconcileRequest,
)
from pydantic import BaseModel

from amodex.model_intelligence.nvidia_diagnostics import NvidiaUpstreamDiagnostics
from amodex.model_intelligence.service import ModelIntelligenceService
from amodex.model_intelligence.vision_benchmark_runner import (
    run_vision_benchmark,
    vision_benchmark_evidence,
)
from amodex.providers.capabilities import WorkerProfileKind
from amodex.providers.catalog import RETIRED_DIRECT_MISTRAL_ROUTE_ID


_logger = logging.getLogger("amodex.api.model_intelligence")


class NvidiaDiagnosticRequest(BaseModel):
    mode: Literal["text", "vision"]


def _raise(exc: ModelIntelligenceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc


def _require_enabled_route(service: ModelIntelligenceService, route_id: str) -> None:
    row = service._repository.get_route(route_id)
    if row is None:
        raise ModelIntelligenceError("ROUTE_NOT_FOUND", route_id, status_code=404)
    if route_id == RETIRED_DIRECT_MISTRAL_ROUTE_ID or not bool(row.get("enabled", True)):
        raise ModelIntelligenceError(
            "ROUTE_DISABLED",
            "the selected model route is retired or disabled",
            status_code=409,
            facts=(route_id,),
        )


def build_model_intelligence_router(service: ModelIntelligenceService) -> APIRouter:
    router = APIRouter(prefix="/v1")
    nvidia_diagnostics = NvidiaUpstreamDiagnostics(service)

    def require_private_diagnostics() -> None:
        # This is an operator diagnostic, not a public free-inference endpoint.
        # The private access_gate validates the session cookie before /v1/.
        if not os.environ.get("AMODEX_ACCESS_PASSCODE", "").strip():
            raise HTTPException(status_code=404, detail="diagnostics unavailable")

    @router.get("/model-intelligence/status")
    def status() -> dict:
        return service.status()

    @router.get("/model-intelligence/routes")
    def routes() -> tuple[dict, ...]:
        # Active UI/API discovery must never depend on a particular frontend
        # version to hide retired routes. Historical evidence remains available
        # through the explicit benchmark evidence endpoint.
        return tuple(
            row for row in service.routes()
            if bool(row.get("enabled", True))
            and str(row.get("route_id")) != RETIRED_DIRECT_MISTRAL_ROUTE_ID
        )

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

    @router.get("/model-intelligence/diagnostics/nvidia")
    def nvidia_diagnostic_configuration(response: Response) -> dict:
        require_private_diagnostics()
        response.headers["Cache-Control"] = "private, no-store"
        return nvidia_diagnostics.configuration()

    @router.post("/model-intelligence/diagnostics/nvidia/run")
    def run_nvidia_diagnostic(payload: NvidiaDiagnosticRequest, response: Response) -> dict:
        require_private_diagnostics()
        response.headers["Cache-Control"] = "private, no-store"
        # Fixed synthetic probe, never user-provided messages or uploads.
        # This is not a benchmark and cannot change routing/measurement state.
        return nvidia_diagnostics.run(mode=payload.mode)

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
    def respond(chat_id: str, payload: ChatRespondRequest) -> dict:
        # FastAPI runs sync handlers in its thread pool. The routed service
        # currently performs synchronous Neon reads/writes around its async
        # provider call; executing its coroutine in a worker keeps those DB
        # calls off Uvicorn's event loop so liveness requests remain responsive.
        # Do not change the service's no-retry-on-ambiguous-outcome semantics.
        try:
            return asyncio.run(
                service.respond(
                    chat_id=chat_id,
                    owner_message_id=payload.owner_message_id,
                    profile=payload.profile,
                )
            )
        except ModelIntelligenceError as exc:
            _raise(exc)

    @router.post("/chats/{chat_id}/respond/recover")
    def recover_response(chat_id: str, payload: ManualChatRecoveryRequest) -> dict:
        """Owner-initiated free alternative for ONE saved ambiguous attempt.

        Never creates a second owner message and never automatically retries
        NVIDIA. Duplicate/concurrent clicks are rejected by an atomic DB claim.
        """
        try:
            return asyncio.run(
                service.respond(
                    chat_id=chat_id,
                    owner_message_id=payload.owner_message_id,
                    profile=None,
                    manual_recovery=True,
                )
            )
        except ModelIntelligenceError as exc:
            _raise(exc)

    @router.get("/chats/{chat_id}/respond/events")
    async def respond_events(
        chat_id: str,
        request: Request,
        owner_message_id: str = Query(min_length=1, max_length=100),
        last_event_id: str | None = Header(default=None),
    ) -> StreamingResponse:
        """Replay stored, redacted stages via SSE; never launches provider calls.

        Last-Event-ID is the stage ordinal, allowing native EventSource reconnects
        to resume instead of repeating already displayed progress.
        """
        try:
            initial = await asyncio.to_thread(
                service.progress_snapshot,
                chat_id=chat_id,
                owner_message_id=owner_message_id,
            )
        except ModelIntelligenceError as exc:
            _raise(exc)
        try:
            from_stage = max(0, min(int(last_event_id or "0"), 30))
        except ValueError:
            from_stage = 0

        async def generate():
            seen = from_stage
            snapshot = initial
            expires = monotonic() + 180.0
            terminal = {"SUCCEEDED", "FAILED", "UNKNOWN_OUTCOME", "BLOCKED"}
            while monotonic() < expires:
                if await request.is_disconnected():
                    break
                for stage_index, item in enumerate(snapshot["phases"], start=1):
                    if stage_index <= seen:
                        continue
                    payload = json.dumps(item, separators=(",", ":"))
                    yield f"id: {stage_index}\ndata: {payload}\n\n"
                    seen = stage_index
                if snapshot["status"] in terminal:
                    result = json.dumps(
                        {"status": snapshot["status"], "decision_id": snapshot["decision_id"]},
                        separators=(",", ":"),
                    )
                    yield f"event: done\ndata: {result}\n\n"
                    return
                yield ": heartbeat\n\n"
                await asyncio.sleep(2.0)
                try:
                    snapshot = await asyncio.to_thread(
                        service.progress_snapshot,
                        chat_id=chat_id,
                        owner_message_id=owner_message_id,
                    )
                except ModelIntelligenceError:
                    break

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "private, no-cache, no-store, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/chats/{chat_id}/model-decisions")
    def chat_decisions(chat_id: str, limit: int = Query(default=100, ge=1, le=500)) -> tuple[dict, ...]:
        return service.decisions(chat_id=chat_id, limit=limit)

    return router
