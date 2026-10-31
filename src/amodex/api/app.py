"""Minimal Stage 1 remote API."""

from __future__ import annotations
import os
from fastapi import FastAPI, HTTPException
from amodex.runtime.models import CreateRunRequest, RunEvent, RunRecord
from amodex.storage.postgres import PostgresRunRepository


def create_app(repository: PostgresRunRepository | None = None) -> FastAPI:
    repo = repository or PostgresRunRepository(os.environ["AMODEX_DATABASE_URL"])
    app = FastAPI(title="Amodex Control Plane", version="0.1-stage1")

    @app.get("/health")
    def health() -> dict[str, str]: return {"status":"ok"}

    @app.post("/v1/runs", response_model=RunRecord, status_code=201)
    def create_run(request: CreateRunRequest) -> RunRecord: return repo.create_run(prompt=request.prompt)

    @app.get("/v1/runs/{run_id}", response_model=RunRecord)
    def get_run(run_id: str) -> RunRecord:
        run = repo.get_run(run_id)
        if run is None: raise HTTPException(status_code=404, detail="run not found")
        return run

    @app.get("/v1/runs/{run_id}/events", response_model=list[RunEvent])
    def get_events(run_id: str) -> list[RunEvent]:
        if repo.get_run(run_id) is None: raise HTTPException(status_code=404, detail="run not found")
        return list(repo.list_events(run_id))

    return app


app = create_app() if os.environ.get("AMODEX_DATABASE_URL") else FastAPI(title="Amodex Control Plane", version="0.1-stage1")
