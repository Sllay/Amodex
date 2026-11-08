"""Deployment/activation health surface for the Amodex web Control Plane.

Liveness intentionally has no external dependency. Readiness verifies the durable
PostgreSQL authority, exact migration checksums, and the fail-closed zero-cost
configuration. Provider availability itself is diagnostic/degraded state and does
not make the whole API unready.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading
import time
from typing import Any

import psycopg
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from amodex.deployment.migrations import discover_production_migrations
from amodex.model_intelligence.service import ModelIntelligenceService
from amodex.providers.capabilities import BillingClass
from amodex.providers.registry import stage5_credential_pools_from_env

_DEFAULT_READINESS_CACHE_SECONDS = 15.0
_MIN_READINESS_CACHE_SECONDS = 1.0
_MAX_READINESS_CACHE_SECONDS = 60.0


def release_identity(*, app_version: str) -> dict[str, str]:
    sha = (
        os.environ.get("RENDER_GIT_COMMIT", "").strip()
        or os.environ.get("AMODEX_RELEASE_SHA", "").strip()
        or "unknown"
    )
    host = "render" if os.environ.get("RENDER_SERVICE_ID", "").strip() else "generic"
    return {"version": app_version, "git_sha": sha, "host": host}


def readiness_cache_seconds() -> float:
    raw = os.environ.get(
        "AMODEX_READINESS_CACHE_SECONDS",
        str(_DEFAULT_READINESS_CACHE_SECONDS),
    ).strip()
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_READINESS_CACHE_SECONDS
    return max(_MIN_READINESS_CACHE_SECONDS, min(_MAX_READINESS_CACHE_SECONDS, value))


def database_readiness(
    dsn: str,
    *,
    migrations_root: str | Path | None = None,
) -> dict[str, Any]:
    if not dsn.strip():
        return {
            "ready": False,
            "reachable": False,
            "schema_compatible": False,
            "expected_migrations": 0,
            "applied_migrations": 0,
            "missing_migrations": 0,
            "checksum_mismatches": 0,
            "reason": "database_url_missing",
        }

    root = Path(
        migrations_root
        or os.environ.get("AMODEX_MIGRATIONS_DIR", "migrations").strip()
        or "migrations"
    )
    try:
        expected = discover_production_migrations(root)
    except Exception:
        return {
            "ready": False,
            "reachable": False,
            "schema_compatible": False,
            "expected_migrations": 0,
            "applied_migrations": 0,
            "missing_migrations": 0,
            "checksum_mismatches": 0,
            "reason": "migration_manifest_unavailable",
        }

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
                cur.execute("SELECT version, checksum_sha256 FROM amodex_schema_migrations")
                rows = cur.fetchall()
    except psycopg.Error:
        return {
            "ready": False,
            "reachable": False,
            "schema_compatible": False,
            "expected_migrations": len(expected),
            "applied_migrations": 0,
            "missing_migrations": len(expected),
            "checksum_mismatches": 0,
            "reason": "database_unavailable_or_schema_missing",
        }

    applied = {str(version): str(checksum or "") for version, checksum in rows}
    missing = [migration.version for migration in expected if migration.version not in applied]
    mismatched = [
        migration.version
        for migration in expected
        if migration.version in applied
        and applied[migration.version] != migration.checksum_sha256
    ]
    compatible = not missing and not mismatched
    return {
        "ready": compatible,
        "reachable": True,
        "schema_compatible": compatible,
        "expected_migrations": len(expected),
        "applied_migrations": len(applied),
        "missing_migrations": len(missing),
        "checksum_mismatches": len(mismatched),
        "reason": "ready" if compatible else "schema_mismatch",
    }


def zero_cost_readiness() -> dict[str, Any]:
    configured: list[str] = []
    violations: list[str] = []
    for pool in stage5_credential_pools_from_env():
        if not os.environ.get(pool.secret_env, "").strip():
            continue
        configured.append(pool.pool_id)
        if pool.billing_class != BillingClass.FREE:
            violations.append(pool.pool_id)

    stt_enabled = os.environ.get("AMODEX_STT_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if stt_enabled:
        stt_billing = os.environ.get("AMODEX_STT_BILLING_CLASS", "unknown").strip().lower()
        if stt_billing != BillingClass.FREE.value:
            violations.append("stt")

    return {
        "ready": not violations,
        "mode": "zero-cost-fail-closed",
        "configured_free_provider_pools": len(configured)
        - len([item for item in violations if item != "stt"]),
        "configured_provider_pools": len(configured),
        "violations": tuple(violations),
        "stt_enabled": stt_enabled,
    }


def activation_snapshot(
    *,
    dsn: str,
    app_version: str,
    model_intelligence_service: ModelIntelligenceService | None = None,
    migrations_root: str | Path | None = None,
) -> dict[str, Any]:
    database = database_readiness(dsn, migrations_root=migrations_root)
    zero_cost = zero_cost_readiness()
    from amodex.deployment.supervised_runtime import status as runtime_status
    supervised = runtime_status()
    ready = bool(database["ready"] and zero_cost["ready"] and (not supervised["enabled"] or supervised["ready"]))
    model_status: dict[str, Any] = {
        "available": model_intelligence_service is not None,
        "automatic_chat_inference": "UNAVAILABLE",
    }
    if model_intelligence_service is not None:
        try:
            status = dict(model_intelligence_service.status())
            model_status = {"available": True, **status}
        except Exception:
            model_status = {
                "available": True,
                "automatic_chat_inference": "DEGRADED",
            }

    return {
        "status": "ready" if ready else "unready",
        "ready": ready,
        "release": release_identity(app_version=app_version),
        "database": database,
        "zero_cost": zero_cost,
        "model_intelligence": model_status,
        "supervised_runtime": supervised,
    }


def build_activation_router(
    *,
    dsn: str,
    app_version: str,
    model_intelligence_service: ModelIntelligenceService | None = None,
) -> APIRouter:
    router = APIRouter(tags=["activation"])
    cache_lock = threading.Lock()
    cached: dict[str, Any] = {"at": 0.0, "snapshot": None}

    def current_snapshot(*, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        ttl = readiness_cache_seconds()
        with cache_lock:
            existing = cached["snapshot"]
            if not force and existing is not None and now - float(cached["at"]) < ttl:
                return existing

        snapshot = activation_snapshot(
            dsn=dsn,
            app_version=app_version,
            model_intelligence_service=model_intelligence_service,
        )
        with cache_lock:
            cached["at"] = now
            cached["snapshot"] = snapshot
        return snapshot

    @router.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/readyz", include_in_schema=False)
    def readyz() -> JSONResponse:
        snapshot = current_snapshot()
        public = {
            "status": snapshot["status"],
            "ready": snapshot["ready"],
            "release": snapshot["release"],
            "database": {
                "reachable": snapshot["database"]["reachable"],
                "schema_compatible": snapshot["database"]["schema_compatible"],
            },
            "zero_cost": {
                "ready": snapshot["zero_cost"]["ready"],
                "mode": snapshot["zero_cost"]["mode"],
            },
        }
        return JSONResponse(public, status_code=200 if snapshot["ready"] else 503)

    @router.get("/v1/activation/status")
    def activation_status() -> dict[str, Any]:
        # The base Control Plane already owns /v1/system/status. A dedicated path
        # avoids route-order collisions while preserving that legacy contract.
        # Access-gate middleware still protects this /v1/* diagnostic in private mode.
        return current_snapshot(force=True)

    return router
