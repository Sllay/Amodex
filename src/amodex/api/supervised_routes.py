"""Private owner routes: durable review/approval, never model-supplied authority."""
from __future__ import annotations

from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from amodex.api.supervised_web import SUPERVISED_HTML
from amodex.runtime.supervised_execution import SupervisedExecutionService
from amodex.runtime.supervised_guard import SupervisedBlocked


class CreateExecution(BaseModel):
    model_config = ConfigDict(extra='forbid')
    draft_id: UUID
    project_id: UUID
    path: str = Field(min_length=1, max_length=240)


class ApproveExecution(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_sha256: str = Field(pattern='^[a-f0-9]{64}$')


def require_owner(request: Request, response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    if getattr(request.state, 'owner_authorized', False) is not True:
        raise HTTPException(403, detail={'code':'PRIVATE_OWNER_SESSION_REQUIRED'})
    if request.method == 'POST':
        origin = urlsplit(request.headers.get('origin', ''))
        if (origin.scheme not in {'http','https'} or origin.netloc != request.headers.get('host')
                or request.headers.get('x-amodex-action') != 'supervised-v1'):
            raise HTTPException(403, detail={'code':'SAME_ORIGIN_APPROVAL_REQUIRED'})


def build_supervised_router(service: SupervisedExecutionService):
    router = APIRouter(dependencies=[Depends(require_owner)])

    @router.get('/supervised', response_class=HTMLResponse, include_in_schema=False)
    def page():
        return HTMLResponse(SUPERVISED_HTML, headers={'Cache-Control':'private, no-store'})

    def require_schema():
        if not service.schema_ready():
            raise HTTPException(503, detail={'code':'SUPERVISED_MIGRATION_REQUIRED'})

    def failure(exc):
        status = 404 if exc.code == 'EXECUTION_NOT_FOUND' else 409 if exc.code in {'IDEMPOTENCY_CONFLICT','DRAFT_NOT_READY_OR_BOUND','SUPERVISED_CAPACITY_BUSY'} else 403
        raise HTTPException(status, detail={'code':exc.code}) from None

    @router.get('/v1/supervised/host-status')
    def host_status():
        from amodex.deployment.supervised_runtime import status
        from amodex.providers.registry import ProviderExecutionRegistry, stage5_credential_pools_from_env
        from amodex.storage.product import PostgresProductRepository
        registry = service.registry or ProviderExecutionRegistry(stage5_credential_pools_from_env())
        pools = registry.configuration_metadata()
        try:
            connections = PostgresProductRepository(service.dsn).list_provider_connections_raw()
            web = [{"provider":r['provider'],"billing_class":r['billing_class'],"enabled":r['enabled']}
                   for r in connections]
            candidate = service.intelligence.supervised_coding_candidate() if service.intelligence else None
            measured = {"provider":candidate.provider,"model":candidate.model} if candidate else None
            code = None
        except Exception:
            web, measured, code = [], None, 'HOST_DIAGNOSTIC_UNCONFIRMED'
        return {"runtime":status(),"process_pools":pools,"web_connections":web,
                "eligible_measured_route":measured,"code":code,"inference_performed":False,
                "note":"Web billing does not bind a process secret or establish quota; execution registry and Governor must still admit."}

    @router.get('/v1/supervised/options')
    def options():
        return service.options()

    @router.get('/v1/supervised/executions')
    def recent():
        require_schema()
        return {'executions':service.recent()}

    @router.post('/v1/supervised/executions', status_code=201)
    def create(body: CreateExecution, request: Request):
        require_schema()
        try:
            return service.create(draft_id=str(body.draft_id), project_id=str(body.project_id),
                                  path=body.path, key=request.headers.get('idempotency-key',''))
        except SupervisedBlocked as exc:
            failure(exc)

    @router.get('/v1/supervised/executions/{execution_id}')
    def get(execution_id: UUID):
        require_schema()
        result = service.get(str(execution_id))
        if result is None:
            raise HTTPException(404, detail={'code':'EXECUTION_NOT_FOUND'})
        return result

    @router.post('/v1/supervised/executions/{execution_id}/approve', status_code=202)
    def approve(execution_id: UUID, body: ApproveExecution, background: BackgroundTasks):
        require_schema()
        try:
            if service.approve(str(execution_id), body.source_sha256):
                background.add_task(service.launch, str(execution_id))
            return service.get(str(execution_id))
        except SupervisedBlocked as exc:
            failure(exc)

    return router
