"""Cloudflare Python Worker adapter for the Stage 1 FastAPI control plane."""

from __future__ import annotations

from fastapi import Request
from workers import asgi

from amodex.api.app import create_app
from amodex.storage.postgres import PostgresRunRepository


def _repository_from_request(request: Request) -> PostgresRunRepository:
    env = request.scope["env"]
    database_url = str(env.DATABASE_URL)
    if not database_url.startswith("postgres"):
        raise RuntimeError("DATABASE_URL binding is missing or invalid")
    return PostgresRunRepository(database_url)


app = create_app(repository_resolver=_repository_from_request, serialize_db=True)


@app.get("/__deployment")
async def deployment(request: Request) -> dict[str, str]:
    env = request.scope["env"]
    return {"build_id": str(env.BUILD_ID), "runtime": "cloudflare-python"}


Default = asgi.entrypoint(app)
