"""Amodex remote API and product projection surfaces."""

from __future__ import annotations

import asyncio
import hmac
import os
import re
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse

from amodex.environments.models import MemoryScope
from amodex.environments.product import (
    EnvironmentCreateRequest,
    MemoryCreateRequest,
    ProjectBindRequest,
    RunnerClaimResponse,
    RunnerDispatchRequest,
    RunnerJobRecord,
    RunnerJobState,
    RunnerKind,
    RunnerReportRequest,
    SessionCreateRequest,
    runner_kind_for_engine,
)
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
from amodex.runners.dispatch import GitHubActionsDispatcher, RunnerDispatchError
from amodex.runtime.models import CreateRunRequest, RunEvent, RunRecord, RunState
from amodex.storage.environment import EnvironmentStateError, PostgresEnvironmentRepository
from amodex.storage.environment_product import PostgresEnvironmentProductRepository
from amodex.storage.postgres import PostgresRunRepository
from amodex.storage.product import PostgresProductRepository, ProductStateError
from .web import DASHBOARD_HTML

RepositoryResolver = Callable[[Request], PostgresRunRepository]
_SECRET_REF_RE = re.compile(r"^env://([A-Z][A-Z0-9_]*)$")
_RELEASE = "0.2-postbaseline-environments"


def create_app(
    repository: PostgresRunRepository | None = None,
    *,
    repository_resolver: RepositoryResolver | None = None,
    product_repository: PostgresProductRepository | None = None,
    environment_repository: PostgresEnvironmentRepository | None = None,
    environment_product_repository: PostgresEnvironmentProductRepository | None = None,
    promotion_executor: ProductPromotionExecutor | None = None,
    runner_dispatcher: GitHubActionsDispatcher | None = None,
    runner_control_token: str | None = None,
    serialize_db: bool = False,
) -> FastAPI:
    """Create the Control Plane without moving authority into CLI/web/runners.

    Stage 1 callers may continue supplying only a run repository. Stage 6 product
    readiness remains backward compatible. Post-baseline Environment surfaces are
    enabled only when their migrations exist and are checked independently.
    """
    if repository is not None and repository_resolver is not None:
        raise ValueError("provide repository or repository_resolver, not both")
    if repository is None and repository_resolver is None:
        dsn = os.environ.get("AMODEX_DATABASE_URL")
        if not dsn:
            raise ValueError("repository or AMODEX_DATABASE_URL is required")
        repository = PostgresRunRepository(dsn)
    if product_repository is None and repository_resolver is None and repository is not None:
        product_repository = PostgresProductRepository(repository.dsn)
    if environment_repository is None and product_repository is not None:
        environment_repository = PostgresEnvironmentRepository(product_repository.dsn)
    if environment_product_repository is None and product_repository is not None:
        environment_product_repository = PostgresEnvironmentProductRepository(product_repository.dsn)

    if promotion_executor is None and product_repository is not None:
        promotion_repo = os.environ.get("AMODEX_PROMOTION_REPOSITORY", "").strip()
        validator = os.environ.get("AMODEX_PROMOTION_VALIDATOR", "").strip()
        if promotion_repo and validator:
            promotion_executor = ConfiguredPromotionExecutor.from_strings(
                repository_path=promotion_repo,
                dsn=product_repository.dsn,
                validator_command=validator,
            )

    if runner_dispatcher is None:
        dispatch_repo = os.environ.get("AMODEX_GITHUB_DISPATCH_REPOSITORY", "").strip()
        dispatch_token = os.environ.get("AMODEX_GITHUB_DISPATCH_TOKEN", "")
        if dispatch_repo and dispatch_token:
            runner_dispatcher = GitHubActionsDispatcher(
                repository=dispatch_repo,
                token=dispatch_token,
                workflow=os.environ.get(
                    "AMODEX_GITHUB_DISPATCH_WORKFLOW", "environment-runner.yml"
                ),
                ref=os.environ.get("AMODEX_GITHUB_DISPATCH_REF", "main"),
            )
    if runner_control_token is None:
        runner_control_token = os.environ.get("AMODEX_RUNNER_CONTROL_TOKEN")

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

    def environment() -> PostgresEnvironmentRepository:
        if environment_repository is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "ENVIRONMENT_STATE_UNAVAILABLE",
                    "message": "Environment state is not configured",
                    "recovery": "configure the Control Plane PostgreSQL repository",
                },
            )
        try:
            if not environment_repository.schema_ready():
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "ENVIRONMENT_SCHEMA_NOT_READY",
                        "message": "Environment isolation schema has not been applied",
                        "recovery": "apply migrations/postbaseline/0001_environment_isolation_budget.sql",
                    },
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="environment database unavailable") from exc
        return environment_repository

    def environment_product() -> PostgresEnvironmentProductRepository:
        if environment_product_repository is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "ENVIRONMENT_PRODUCT_UNAVAILABLE", "message": "runner job state is not configured"},
            )
        try:
            if not environment_product_repository.schema_ready():
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "ENVIRONMENT_PRODUCT_SCHEMA_NOT_READY",
                        "message": "Environment runner schema has not been applied",
                        "recovery": "apply migrations/postbaseline/0002_environment_product_runners.sql",
                    },
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="environment product database unavailable") from exc
        return environment_product_repository

    def authorize_runner(request: Request) -> None:
        if not runner_control_token:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "RUNNER_AUTH_NOT_CONFIGURED",
                    "message": "AMODEX_RUNNER_CONTROL_TOKEN is not configured",
                },
            )
        supplied = request.headers.get("authorization", "")
        expected = f"Bearer {runner_control_token}"
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="invalid runner authorization")

    def environment_error(exc: Exception) -> HTTPException:
        if isinstance(exc, KeyError):
            return HTTPException(status_code=404, detail="environment resource not found")
        if isinstance(exc, EnvironmentStateError):
            return HTTPException(
                status_code=409,
                detail={"code": exc.code, "message": exc.detail},
            )
        return HTTPException(status_code=409, detail=str(exc))

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
        env_ready = False
        runner_ready = False
        try:
            env_ready = bool(environment_repository and environment_repository.schema_ready())
            runner_ready = bool(
                environment_product_repository and environment_product_repository.schema_ready()
            )
        except Exception:
            pass
        return {
            "release": _RELEASE,
            "database": "HEALTHY" if repo.ping() else "UNAVAILABLE",
            "schema_ready": repo.schema_ready(),
            "zero_cost_mode": zero_cost,
            "environment_schema_ready": env_ready,
            "runner_schema_ready": runner_ready,
            "actions_dispatch_configured": runner_dispatcher is not None,
            "runner_auth_configured": bool(runner_control_token),
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
        if body.project_id is not None and product().get_project(body.project_id) is None:
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

    @app.post("/v1/environments", status_code=201)
    async def create_environment(body: EnvironmentCreateRequest) -> dict[str, object]:
        item = environment().create_environment(name=body.name, engine=body.engine)
        product().append_audit(
            actor="owner",
            action="ENVIRONMENT_CREATED",
            subject_type="environment",
            subject_id=item.environment_id,
            detail={"name": item.name, "engine": item.engine},
        )
        return jsonable_encoder(asdict(item))

    @app.get("/v1/environments")
    async def list_environments() -> list[dict[str, object]]:
        return [jsonable_encoder(asdict(item)) for item in environment().list_environments()]

    @app.get("/v1/environments/{environment_id}")
    async def get_environment(environment_id: str) -> dict[str, object]:
        item = environment().get_environment(environment_id)
        if item is None:
            raise HTTPException(status_code=404, detail="environment not found")
        return jsonable_encoder(asdict(item))

    @app.post("/v1/environments/{environment_id}/projects")
    async def bind_environment_project(
        environment_id: str,
        body: ProjectBindRequest,
    ) -> dict[str, object]:
        try:
            environment().bind_project(environment_id=environment_id, project_id=body.project_id)
        except (KeyError, EnvironmentStateError) as exc:
            raise environment_error(exc) from exc
        product().append_audit(
            actor="owner",
            action="ENVIRONMENT_PROJECT_BOUND",
            subject_type="project",
            subject_id=body.project_id,
            detail={"environment_id": environment_id},
        )
        return {"environment_id": environment_id, "project_id": body.project_id, "bound": True}

    @app.post("/v1/environments/{environment_id}/sessions", status_code=201)
    async def create_environment_session(
        environment_id: str,
        body: SessionCreateRequest,
    ) -> dict[str, object]:
        try:
            item = environment().create_session(
                environment_id=environment_id,
                title=body.title,
                domain_key=body.domain_key,
            )
        except (KeyError, EnvironmentStateError) as exc:
            raise environment_error(exc) from exc
        return jsonable_encoder(asdict(item))

    @app.get("/v1/environments/{environment_id}/sessions")
    async def list_environment_sessions(environment_id: str) -> list[dict[str, object]]:
        return [
            jsonable_encoder(asdict(item))
            for item in environment().list_sessions(environment_id=environment_id)
        ]

    @app.post("/v1/environments/{environment_id}/sessions/{session_id}/runs/{run_id}")
    async def bind_environment_run(
        environment_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, object]:
        try:
            environment().bind_run(
                environment_id=environment_id,
                session_id=session_id,
                run_id=run_id,
            )
        except (KeyError, EnvironmentStateError) as exc:
            raise environment_error(exc) from exc
        return {
            "environment_id": environment_id,
            "session_id": session_id,
            "run_id": run_id,
            "bound": True,
        }

    @app.post("/v1/environments/{environment_id}/memories", status_code=201)
    async def create_environment_memory(
        environment_id: str,
        body: MemoryCreateRequest,
    ) -> dict[str, object]:
        scope = MemoryScope(body.scope)
        if scope == MemoryScope.OWNER:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "OWNER_MEMORY_NOT_ENVIRONMENT_WRITABLE",
                    "message": "owner memory is intentionally outside ordinary Environment write authority",
                },
            )
        try:
            item = environment().put_memory(
                scope=scope,
                content=body.content,
                source_ref=body.source_ref,
                environment_id=environment_id,
                session_id=body.session_id,
                domain_key=body.domain_key,
            )
        except (KeyError, EnvironmentStateError, ValueError) as exc:
            raise environment_error(exc) from exc
        return jsonable_encoder(asdict(item))

    @app.get("/v1/environments/{environment_id}/memories")
    async def list_environment_memories(
        environment_id: str,
        session_id: str | None = None,
        domain_key: str | None = None,
        include_owner: bool = True,
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> list[dict[str, object]]:
        rows = environment().list_visible_memories(
            environment_id=environment_id,
            session_id=session_id,
            domain_key=domain_key,
            include_owner=include_owner,
            limit=limit,
        )
        return [jsonable_encoder(asdict(item)) for item in rows]

    @app.get("/v1/environments/{environment_id}/usage")
    async def environment_usage(environment_id: str) -> dict[str, object]:
        if environment().get_environment(environment_id) is None:
            raise HTTPException(status_code=404, detail="environment not found")
        return environment_product().usage_projection(
            environment_id=environment_id
        ).model_dump(mode="json")

    @app.post("/v1/environments/{environment_id}/runner-jobs", status_code=201)
    async def dispatch_environment_runner(
        environment_id: str,
        body: RunnerDispatchRequest,
    ) -> dict[str, object]:
        env = environment().get_environment(environment_id)
        if env is None:
            raise HTTPException(status_code=404, detail="environment not found")
        kind = runner_kind_for_engine(env.engine)
        if kind == RunnerKind.ACTIONS_HEADLESS and runner_dispatcher is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "ACTIONS_DISPATCH_UNAVAILABLE",
                    "message": "GitHub Actions dispatcher is not configured",
                    "recovery": "configure AMODEX_GITHUB_DISPATCH_REPOSITORY and AMODEX_GITHUB_DISPATCH_TOKEN",
                },
            )
        try:
            job = environment_product().create_runner_job(
                environment_id=environment_id,
                session_id=body.session_id,
                run_id=body.run_id,
                worker_profile=body.worker_profile,
                task_class=body.task_class,
                write_paths=body.write_paths,
            )
        except (KeyError, EnvironmentStateError) as exc:
            raise environment_error(exc) from exc

        if job.runner_kind == RunnerKind.DESKTOP_STUDIO:
            product().append_audit(
                actor="owner",
                action="DESKTOP_RUNNER_JOB_QUEUED",
                subject_type="runner_job",
                subject_id=job.job_id,
                detail={
                    "environment_id": environment_id,
                    "engine": job.engine,
                    "reason": "Roblox Studio MCP requires a connected desktop Studio runner",
                },
            )
            return {
                "job": job.model_dump(mode="json"),
                "dispatch": "DESKTOP_RUNNER_REQUIRED",
            }

        assert runner_dispatcher is not None
        try:
            runner_dispatcher.dispatch(job_id=job.job_id)
        except RunnerDispatchError as exc:
            unknown = environment_product().mark_dispatch_unknown(
                job_id=job.job_id,
                summary=str(exc),
            )
            product().append_audit(
                actor="control-plane",
                action="RUNNER_DISPATCH_UNKNOWN",
                subject_type="runner_job",
                subject_id=job.job_id,
                detail={"message": str(exc)[:1000]},
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "RUNNER_DISPATCH_UNKNOWN",
                    "message": "GitHub dispatch outcome is ambiguous; automatic redispatch is forbidden",
                    "job_id": job.job_id,
                    "state": unknown.state.value,
                },
            ) from exc
        dispatched = environment_product().mark_dispatched(job_id=job.job_id)
        product().append_audit(
            actor="control-plane",
            action="RUNNER_DISPATCHED",
            subject_type="runner_job",
            subject_id=job.job_id,
            detail={"runner_kind": dispatched.runner_kind.value, "engine": dispatched.engine},
        )
        return {"job": dispatched.model_dump(mode="json"), "dispatch": "GITHUB_ACTIONS"}

    @app.get("/v1/environments/{environment_id}/runner-jobs")
    async def list_environment_runner_jobs(environment_id: str) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json")
            for item in environment_product().list_runner_jobs(environment_id=environment_id)
        ]

    @app.get("/v1/runner-jobs/{job_id}", response_model=RunnerJobRecord)
    async def get_runner_job(job_id: str) -> RunnerJobRecord:
        item = environment_product().get_runner_job(job_id)
        if item is None:
            raise HTTPException(status_code=404, detail="runner job not found")
        return item

    @app.post("/v1/runner-jobs/{job_id}/claim", response_model=RunnerClaimResponse)
    async def claim_runner_job(
        job_id: str,
        request: Request,
        github_run_id: str | None = Query(default=None, max_length=100),
    ) -> RunnerClaimResponse:
        authorize_runner(request)
        try:
            payload = environment_product().claim_runner_job(
                job_id=job_id,
                github_run_id=github_run_id,
            )
        except (KeyError, EnvironmentStateError) as exc:
            raise environment_error(exc) from exc
        product().append_audit(
            actor="runner",
            action="RUNNER_JOB_CLAIMED",
            subject_type="runner_job",
            subject_id=job_id,
            detail={"github_run_id": github_run_id},
        )
        return RunnerClaimResponse(**payload)

    @app.post("/v1/runner-jobs/{job_id}/running", response_model=RunnerJobRecord)
    async def mark_runner_job_running(job_id: str, request: Request) -> RunnerJobRecord:
        authorize_runner(request)
        try:
            return environment_product().mark_running(job_id=job_id)
        except (KeyError, EnvironmentStateError) as exc:
            raise environment_error(exc) from exc

    @app.post("/v1/runner-jobs/{job_id}/report", response_model=RunnerJobRecord)
    async def report_runner_job(
        job_id: str,
        body: RunnerReportRequest,
        request: Request,
    ) -> RunnerJobRecord:
        authorize_runner(request)
        try:
            item = environment_product().report_runner_job(
                job_id=job_id,
                state=body.state,
                summary=body.summary,
                result_commit=body.result_commit,
                github_run_id=body.github_run_id,
            )
        except (KeyError, EnvironmentStateError) as exc:
            raise environment_error(exc) from exc
        product().append_audit(
            actor="runner",
            action=f"RUNNER_JOB_{item.state.value}",
            subject_type="runner_job",
            subject_id=job_id,
            detail={
                "summary": body.summary[:1000],
                "result_commit": body.result_commit,
                "evidence": body.evidence,
            },
        )
        return item

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

        environments: list[dict[str, object]] = []
        sessions: list[dict[str, object]] = []
        usages: list[dict[str, object]] = []
        runner_jobs: list[dict[str, object]] = []
        try:
            if (
                environment_repository is not None
                and environment_product_repository is not None
                and environment_repository.schema_ready()
                and environment_product_repository.schema_ready()
            ):
                env_rows = environment_repository.list_environments()
                environments = [jsonable_encoder(asdict(item)) for item in env_rows]
                for env in env_rows:
                    sessions.extend(
                        jsonable_encoder(asdict(item))
                        for item in environment_repository.list_sessions(
                            environment_id=env.environment_id
                        )
                    )
                    usages.append(
                        environment_product_repository.usage_projection(
                            environment_id=env.environment_id
                        ).model_dump(mode="json")
                    )
                runner_jobs = [
                    item.model_dump(mode="json")
                    for item in environment_product_repository.list_runner_jobs(limit=200)
                ]
        except Exception as exc:
            blockers.append(
                {
                    "kind": "ENVIRONMENT_PROJECTION_UNAVAILABLE",
                    "detail": type(exc).__name__,
                    "subject_id": "environment-product",
                }
            )

        for item in runner_jobs:
            if item["state"] == RunnerJobState.DISPATCH_UNKNOWN.value:
                blockers.append(
                    {
                        "kind": "RUNNER_DISPATCH_UNKNOWN",
                        "detail": item.get("summary") or "dispatch outcome ambiguous",
                        "subject_id": item["job_id"],
                    }
                )

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
            environments=environments,
            environment_sessions=sessions,
            environment_usage=usages,
            environment_runner_jobs=runner_jobs,
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
