"""Amodex remote API and Stage 6 product projection surfaces."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from amodex.product.models import (
    ApprovalCreateRequest,
    ApprovalRecord,
    DashboardSnapshot,
    ProjectCreateRequest,
    ProjectRecord,
    ProviderConnectionRequest,
    RunControl,
    RunControlRequest,
    RunDesiredState,
    RunnerHeartbeatRequest,
    RunnerRecord,
)
from amodex.product.promotion import ConfiguredPromotionExecutor, ProductPromotionExecutor
from amodex.runtime.models import CreateRunRequest, RunEvent, RunRecord, RunState
from amodex.storage.postgres import PostgresRunRepository
from amodex.storage.product import PostgresProductRepository, ProductStateError
from .web import DASHBOARD_HTML

RepositoryResolver = Callable[[Request], PostgresRunRepository]
_SECRET_REF_RE = re.compile(r"^env://([A-Z][A-Z0-9_]*)$")
_RELEASE = "0.1-stage6"


def create_app(
    repository: PostgresRunRepository | None = None,
    *,
    repository_resolver: RepositoryResolver | None = None,
    product_repository: PostgresProductRepository | None = None,
    promotion_executor: ProductPromotionExecutor | None = None,
    serialize_db: bool = False,
) -> FastAPI:
    """Create the API without moving authority into CLI/web clients.

    Stage 1 callers may continue supplying only a run repository. Stage 6 product
    endpoints become available when a ProductRepository is supplied or when the
    conventional database environment is configured.
    """
    if repository is not None and repository_resolver is not None:
        raise ValueError("provide repository or repository_resolver, not both")
    if repository is None and repository_resolver is None:
        dsn = os.environ.get("AMODEX_DATABASE_URL")
        if not dsn:
            raise ValueError("repository or AMODEX_DATABASE_URL is required")
        repository = PostgresRunRepository(dsn)
    if product_repository is None and repository_resolver is None and repository is not None:
        # Product state is enabled only when its migration exists; creating the
        # repository object itself performs no migration or write.
        product_repository = PostgresProductRepository(repository.dsn)

    if promotion_executor is None and product_repository is not None:
        promotion_repo = os.environ.get("AMODEX_PROMOTION_REPOSITORY", "").strip()
        validator = os.environ.get("AMODEX_PROMOTION_VALIDATOR", "").strip()
        if promotion_repo and validator:
            promotion_executor = ConfiguredPromotionExecutor.from_strings(
                repository_path=promotion_repo,
                dsn=product_repository.dsn,
                validator_command=validator,
            )

    db_lock = asyncio.Lock() if serialize_db else None
    app = FastAPI(title="Amodex Control Plane", version=_RELEASE)

    def resolve_repository(request: Request) -> PostgresRunRepository:
        if repository_resolver is not None:
            return repository_resolver(request)
        assert repository is not None
        return repository

    async def database_call(request: Request, operation):
        repo = resolve_repository(request)
        if db_lock is None:
            return operation(repo)
        async with db_lock:
            return operation(repo)

    def product() -> PostgresProductRepository:
        if product_repository is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "PRODUCT_STATE_UNAVAILABLE",
                    "message": "Stage 6 product state is not configured",
                    "recovery": "configure AMODEX_DATABASE_URL and apply migrations/stage6",
                },
            )
        try:
            if not product_repository.schema_ready():
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "PRODUCT_SCHEMA_NOT_READY",
                        "message": "Stage 6 schema has not been applied",
                        "recovery": "run the documented Stage 6 migration job before serving product traffic",
                    },
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "PRODUCT_DATABASE_UNAVAILABLE",
                    "message": "product database is unavailable",
                    "recovery": "check PostgreSQL connectivity and the Stage 6 operations runbook",
                },
            ) from exc
        return product_repository

    @app.get("/", response_class=HTMLResponse)
    async def dashboard_page() -> str:
        return DASHBOARD_HTML

    @app.get("/health")
    async def health() -> dict[str, object]:
        db_ok = product_repository.ping() if product_repository is not None else True
        return {"status": "ok" if db_ok else "degraded", "release": _RELEASE, "database": db_ok}

    @app.get("/ready")
    async def readiness() -> dict[str, object]:
        if product_repository is None:
            return {"ready": True, "release": _RELEASE, "product_surface": False}
        ready = product_repository.ping() and product_repository.schema_ready()
        if not ready:
            raise HTTPException(status_code=503, detail="database/schema not ready")
        return {"ready": True, "release": _RELEASE, "product_surface": True}

    @app.get("/v1/system/status")
    async def system_status() -> dict[str, object]:
        repo = product()
        settings = repo.settings()
        zero_cost = not bool(settings.get("zero_cost_policy", {}).get("paid_inference_allowed", False))
        return {
            "release": _RELEASE,
            "database": "HEALTHY" if repo.ping() else "UNAVAILABLE",
            "schema_ready": repo.schema_ready(),
            "zero_cost_mode": zero_cost,
            "settings": settings,
        }

    @app.post("/v1/projects", response_model=ProjectRecord, status_code=201)
    async def create_project(body: ProjectCreateRequest) -> ProjectRecord:
        repo = product()
        name = (body.name or _project_name(body.repo_url)).strip()
        try:
            return repo.create_project(repo_url=body.repo_url.strip(), name=name)
        except ProductStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/projects", response_model=list[ProjectRecord])
    async def list_projects() -> list[ProjectRecord]:
        return list(product().list_projects())

    @app.post("/v1/runs", response_model=RunRecord, status_code=201)
    async def create_run(body: CreateRunRequest, request: Request) -> RunRecord:
        if body.project_id is not None:
            if product().get_project(body.project_id) is None:
                raise HTTPException(status_code=404, detail="project not found")
        return await database_call(
            request,
            lambda repo: repo.create_run(prompt=body.prompt, project_id=body.project_id),
        )

    @app.get("/v1/runs", response_model=list[RunRecord])
    async def list_runs(
        request: Request,
        project_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[RunRecord]:
        return list(await database_call(request, lambda repo: repo.list_runs(project_id=project_id, limit=limit)))

    @app.get("/v1/runs/{run_id}", response_model=RunRecord)
    async def get_run(run_id: str, request: Request) -> RunRecord:
        run = await database_call(request, lambda repo: repo.get_run(run_id))
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    @app.get("/v1/runs/{run_id}/events", response_model=list[RunEvent])
    async def get_events(run_id: str, request: Request) -> list[RunEvent]:
        exists = await database_call(request, lambda repo: repo.get_run(run_id))
        if exists is None:
            raise HTTPException(status_code=404, detail="run not found")
        return list(await database_call(request, lambda repo: repo.list_events(run_id)))

    async def change_run_control(run_id: str, desired: RunDesiredState, body: RunControlRequest) -> RunControl:
        repo = product()
        try:
            return repo.set_run_control(run_id=run_id, desired_state=desired, reason=body.reason)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except ProductStateError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "RUN_CONTROL_CONFLICT", "message": str(exc), "recovery": "refresh run status before retrying"},
            ) from exc

    @app.post("/v1/runs/{run_id}/pause", response_model=RunControl)
    async def pause_run(run_id: str, body: RunControlRequest) -> RunControl:
        return await change_run_control(run_id, RunDesiredState.PAUSED, body)

    @app.post("/v1/runs/{run_id}/resume", response_model=RunControl)
    async def resume_run(run_id: str, body: RunControlRequest) -> RunControl:
        return await change_run_control(run_id, RunDesiredState.ACTIVE, body)

    @app.post("/v1/runs/{run_id}/cancel", response_model=RunControl)
    async def cancel_run(run_id: str, body: RunControlRequest) -> RunControl:
        return await change_run_control(run_id, RunDesiredState.CANCELLED, body)

    @app.post("/v1/approvals", response_model=ApprovalRecord, status_code=201)
    async def create_approval(body: ApprovalCreateRequest) -> ApprovalRecord:
        return product().create_approval(
            action=body.action,
            subject_type=body.subject_type,
            subject_id=body.subject_id,
            impact=body.impact,
            expires_at=body.expires_at,
        )

    @app.get("/v1/approvals", response_model=list[ApprovalRecord])
    async def list_approvals() -> list[ApprovalRecord]:
        return list(product().list_approvals())

    @app.post("/v1/approvals/{approval_id}/approve", response_model=ApprovalRecord)
    async def approve_action(approval_id: str) -> ApprovalRecord:
        try:
            return product().resolve_approval(approval_id=approval_id, approve=True)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="approval not found") from exc
        except ProductStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/approvals/{approval_id}/reject", response_model=ApprovalRecord)
    async def reject_action(approval_id: str) -> ApprovalRecord:
        try:
            return product().resolve_approval(approval_id=approval_id, approve=False)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="approval not found") from exc
        except ProductStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/provider-connections")
    async def connect_provider(body: ProviderConnectionRequest) -> dict[str, object]:
        match = _SECRET_REF_RE.fullmatch(body.secret_ref)
        if match is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "SECRET_REFERENCE_REQUIRED",
                    "message": "raw provider secrets are not accepted by this endpoint",
                    "recovery": "store the secret in the deployment secret manager and submit env://VARIABLE_NAME",
                },
            )
        env_name = match.group(1)
        row = product().upsert_provider_connection(
            provider=body.provider.strip().lower(),
            credential_pool_id=body.credential_pool_id,
            secret_ref=body.secret_ref,
            billing_class=body.billing_class,
            enabled=body.enabled,
        )
        return _connection_projection(row, secret_present=bool(os.environ.get(env_name)))

    @app.get("/v1/provider-connections")
    async def provider_connections() -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for row in product().list_provider_connections_raw():
            match = _SECRET_REF_RE.fullmatch(row["secret_ref"])
            present = bool(match and os.environ.get(match.group(1)))
            result.append(_connection_projection(row, secret_present=present))
        return result

    @app.post("/v1/runners/{runner_id}/heartbeat", response_model=RunnerRecord)
    async def runner_heartbeat(runner_id: str, body: RunnerHeartbeatRequest) -> RunnerRecord:
        return product().heartbeat_runner(
            runner_id=runner_id,
            profile=body.profile,
            state=body.state,
            capabilities=body.capabilities,
            detail=body.detail,
        )

    @app.get("/v1/runners", response_model=list[RunnerRecord])
    async def runners() -> list[RunnerRecord]:
        return list(product().list_runners())

    @app.get("/v1/runs/{run_id}/tasks")
    async def task_views(run_id: str) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in product().list_task_views(run_id=run_id)]

    @app.get("/v1/checkpoints")
    async def checkpoint_views() -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in product().list_checkpoint_views()]

    @app.get("/v1/evidence")
    async def evidence_views() -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in product().list_evidence_views()]

    @app.get("/v1/audit")
    async def audit(limit: int = Query(default=200, ge=1, le=1000)) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in product().list_audit(limit=limit)]

    @app.get("/v1/operations/retention/preview")
    async def retention_preview(days: int = Query(default=30, ge=1, le=3650)) -> dict[str, int]:
        return product().retention_preview(terminal_run_days=days)

    @app.post("/v1/candidates/{candidate_id}/promote")
    async def promote_candidate(candidate_id: str) -> dict[str, object]:
        if promotion_executor is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "PROMOTION_EXECUTOR_UNAVAILABLE",
                    "message": "promotion execution is not configured on this Control Plane",
                    "recovery": "configure AMODEX_PROMOTION_REPOSITORY and AMODEX_PROMOTION_VALIDATOR on the trusted control plane",
                },
            )
        idempotency_key = f"stage6-owner:{candidate_id}"
        try:
            result = promotion_executor.promote(candidate_id=candidate_id, idempotency_key=idempotency_key)
        except Exception as exc:
            product().append_audit(
                actor="owner",
                action="PROMOTION_REQUEST_FAILED",
                subject_type="candidate",
                subject_id=candidate_id,
                detail={"error_class": type(exc).__name__, "message": str(exc)[:1000]},
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        product().append_audit(
            actor="owner",
            action="PROMOTION_REQUEST_COMPLETED",
            subject_type="candidate",
            subject_id=candidate_id,
            detail=dict(result),
        )
        return result

    @app.get("/v1/dashboard", response_model=DashboardSnapshot)
    async def dashboard(request: Request) -> DashboardSnapshot:
        product_repo = product()
        runs = list(await database_call(request, lambda repo: repo.list_runs(limit=100)))
        projects = [item.model_dump(mode="json") for item in product_repo.list_projects()]
        tasks = [item.model_dump(mode="json") for item in product_repo.list_task_views()]
        runners_rows = [item.model_dump(mode="json") for item in product_repo.list_runners()]
        checkpoints = [item.model_dump(mode="json") for item in product_repo.list_checkpoint_views()]
        evidence = [item.model_dump(mode="json") for item in product_repo.list_evidence_views()]
        approvals = [item.model_dump(mode="json") for item in product_repo.list_approvals()]
        audit_rows = [item.model_dump(mode="json") for item in product_repo.list_audit(limit=150)]

        connections = await provider_connections()
        availability = list(product_repo.provider_status_rows())
        provider_rows = _merge_provider_status(connections, availability)
        blockers: list[dict[str, object]] = []
        for item in approvals:
            if item["status"] == "PENDING":
                blockers.append({"kind": "APPROVAL", "detail": item["impact"], "subject_id": item["approval_id"]})
        for item in runs:
            if item.state in {RunState.BLOCKED, RunState.UNKNOWN_OUTCOME, RunState.WAITING_PROVIDER}:
                blockers.append({"kind": item.state.value, "detail": item.last_error_code or item.state.value, "subject_id": item.run_id})
        for item in connections:
            if not item["secret_present"]:
                blockers.append({"kind": "PROVIDER_SECRET_MISSING", "detail": item["provider"], "subject_id": item["provider"]})
        for item in provider_rows:
            if item.get("quota_available") is False:
                blockers.append({"kind": "PROVIDER_QUOTA", "detail": item.get("reason", "quota unavailable"), "subject_id": item.get("provider")})

        timeline = list(audit_rows)
        for run in runs[:25]:
            events = await database_call(request, lambda repo, rid=run.run_id: repo.list_events(rid))
            timeline.extend(event.model_dump(mode="json") for event in events[-12:])
        timeline.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)

        settings = product_repo.settings()
        zero_cost = not bool(settings.get("zero_cost_policy", {}).get("paid_inference_allowed", False))
        return DashboardSnapshot(
            generated_at=datetime.now(timezone.utc),
            release=_RELEASE,
            zero_cost_mode=zero_cost,
            projects=projects,
            runs=[item.model_dump(mode="json") for item in runs],
            tasks=tasks,
            providers=provider_rows,
            runners=runners_rows,
            checkpoints=checkpoints,
            candidates=list(product_repo.candidate_rows()),
            promotions=list(product_repo.promotion_rows()),
            evidence=evidence,
            approvals=approvals,
            blockers=blockers,
            timeline=timeline[:300],
        )

    return app


def _project_name(repo_url: str) -> str:
    clean = repo_url.rstrip("/")
    name = clean.rsplit("/", 1)[-1]
    return name.removesuffix(".git") or "project"


def _connection_projection(row: dict[str, object], *, secret_present: bool) -> dict[str, object]:
    return {
        "provider": row["provider"],
        "credential_pool_id": row["credential_pool_id"],
        "secret_ref": row["secret_ref"],
        "billing_class": row["billing_class"],
        "enabled": bool(row["enabled"]),
        "secret_present": secret_present,
        "connection_state": "CONNECTED" if secret_present and row["enabled"] else "MISSING_SECRET" if not secret_present else "DISABLED",
        "updated_at": row["updated_at"],
    }


def _merge_provider_status(
    connections: list[dict[str, object]],
    availability: list[dict[str, object]],
) -> list[dict[str, object]]:
    by_key = {(str(item["provider"]), str(item["credential_pool_id"])): dict(item) for item in availability}
    for connection in connections:
        key = (str(connection["provider"]), str(connection["credential_pool_id"]))
        merged = by_key.get(key, {})
        merged.update(connection)
        by_key[key] = merged
    return [by_key[key] for key in sorted(by_key)]


if os.environ.get("AMODEX_DATABASE_URL"):
    app = create_app()
else:
    app = FastAPI(title="Amodex Control Plane", version=_RELEASE)
