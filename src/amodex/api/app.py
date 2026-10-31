"""Minimal Stage 1 remote API shared by local/server and edge adapters."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Request

from amodex.runtime.models import CreateRunRequest, RunEvent, RunRecord
from amodex.storage.postgres import PostgresRunRepository

RepositoryResolver = Callable[[Request], PostgresRunRepository]


def create_app(
    repository: PostgresRunRepository | None = None,
    *,
    repository_resolver: RepositoryResolver | None = None,
    serialize_db: bool = False,
) -> FastAPI:
    """Create the Stage 1 API without duplicating transport-specific authority.

    A fixed repository is used by conventional servers/tests. Edge runtimes can
    supply a request-scoped resolver because their bindings are only available
    inside a request. Cloudflare's synchronous PostgreSQL drivers are serialized
    with an asyncio lock as required by the Python Workers runtime guidance.
    """
    if repository is not None and repository_resolver is not None:
        raise ValueError("provide repository or repository_resolver, not both")
    if repository is None and repository_resolver is None:
        dsn = os.environ.get("AMODEX_DATABASE_URL")
        if not dsn:
            raise ValueError("repository or AMODEX_DATABASE_URL is required")
        repository = PostgresRunRepository(dsn)

    db_lock = asyncio.Lock() if serialize_db else None
    app = FastAPI(title="Amodex Control Plane", version="0.1-stage1")

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

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/runs", response_model=RunRecord, status_code=201)
    async def create_run(body: CreateRunRequest, request: Request) -> RunRecord:
        return await database_call(request, lambda repo: repo.create_run(prompt=body.prompt))

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
        events = await database_call(request, lambda repo: repo.list_events(run_id))
        return list(events)

    return app


if os.environ.get("AMODEX_DATABASE_URL"):
    app = create_app()
else:
    app = FastAPI(title="Amodex Control Plane", version="0.1-stage1")
