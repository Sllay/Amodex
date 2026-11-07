"""Durable product/runner state layered on Environment isolation."""

from __future__ import annotations

import json
import hashlib
from typing import Any

import psycopg
from psycopg.rows import dict_row

from amodex.contracts.ids import new_uuid7, validate_uuid7
from amodex.environments.product import (
    RunnerJobRecord,
    RunnerJobState,
    RunnerKind,
    UsageProjection,
    capabilities_for_engine,
    runner_kind_for_engine,
    require_github_run_id,
    require_idempotency_key,
)
from amodex.providers.budget import TaskBudgetClass
from amodex.storage.environment import EnvironmentStateError


class PostgresEnvironmentProductRepository:
    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    @property
    def dsn(self) -> str:
        return self._dsn

    def schema_ready(self) -> bool:
        with psycopg.connect(self._dsn) as conn:
            row = conn.execute(
                """
                SELECT
                    to_regclass('public.amodex_environment_runner_jobs') IS NOT NULL
                    AND EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema='public'
                          AND table_name='amodex_environment_runner_jobs'
                          AND column_name='dispatch_status'
                    )
                    AND EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema='public' AND table_name='amodex_environment_runner_jobs'
                          AND column_name='lease_expires_at'
                    )
                """
            ).fetchone()
        return bool(row and row[0])

    def create_runner_job(
        self,
        *,
        environment_id: str,
        session_id: str,
        run_id: str,
        worker_profile: str,
        task_class: TaskBudgetClass,
        write_paths: tuple[str, ...],
        idempotency_key: str,
    ) -> RunnerJobRecord:
        require_idempotency_key(idempotency_key)
        environment_id, session_id, run_id = map(validate_uuid7, (environment_id, session_id, run_id))
        # Explicit defaults, unordered write scope, and Environment are part of intent.
        digest = hashlib.sha256(json.dumps({
            "version": 1, "environment_id": environment_id, "session_id": session_id,
            "run_id": run_id, "worker_profile": worker_profile, "task_class": task_class.value,
            "write_paths": sorted(write_paths),
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                existing = conn.execute(
                    "SELECT * FROM amodex_environment_runner_jobs WHERE environment_id=%s AND idempotency_key=%s",
                    (environment_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    return _require_matching_intent(existing, digest)
                row = conn.execute(
                    """
                    SELECT
                        e.environment_id,
                        e.engine,
                        e.status AS environment_status,
                        s.session_id,
                        s.status AS session_status,
                        r.run_id,
                        r.project_id,
                        p.repo_url,
                        ep.environment_id AS project_environment,
                        sr.environment_id AS run_environment,
                        sr.session_id AS run_session
                    FROM amodex_environments e
                    JOIN amodex_sessions s
                      ON s.environment_id=e.environment_id AND s.session_id=%s
                    JOIN amodex_runs r ON r.run_id=%s
                    JOIN amodex_projects p ON p.project_id=r.project_id
                    LEFT JOIN amodex_environment_projects ep ON ep.project_id=r.project_id
                    LEFT JOIN amodex_session_runs sr ON sr.run_id=r.run_id
                    WHERE e.environment_id=%s
                    FOR SHARE OF e,s,r,p
                    """,
                    (session_id, run_id, environment_id),
                ).fetchone()
                if row is None:
                    raise EnvironmentStateError(
                        "RUNNER_SCOPE_NOT_FOUND",
                        f"environment={environment_id} session={session_id} run={run_id}",
                    )
                if row["environment_status"] != "ACTIVE" or row["session_status"] != "ACTIVE":
                    raise EnvironmentStateError("RUNNER_SCOPE_INACTIVE", environment_id)
                if row["project_environment"] is None or str(row["project_environment"]) != environment_id:
                    raise EnvironmentStateError("PROJECT_ENVIRONMENT_MISMATCH", str(row["project_id"]))
                if row["run_environment"] is None:
                    raise EnvironmentStateError("RUN_NOT_BOUND_TO_SESSION", run_id)
                if str(row["run_environment"]) != environment_id or str(row["run_session"]) != session_id:
                    raise EnvironmentStateError("RUN_SESSION_MISMATCH", run_id)

                engine = row["engine"]
                runner_kind = runner_kind_for_engine(engine)
                job_id = new_uuid7()
                branch_name = f"amodex/{job_id}"
                created = conn.execute(
                    """
                    INSERT INTO amodex_environment_runner_jobs(
                        job_id,environment_id,session_id,run_id,project_id,engine,
                        runner_kind,worker_profile,task_class,write_paths,state,branch_name,
                        created_at,updated_at,idempotency_key,intent_digest,dispatch_attempt_id,dispatch_status
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'QUEUED',%s,NOW(),NOW(),%s,%s,%s,%s)
                    ON CONFLICT (environment_id,idempotency_key) DO NOTHING
                    RETURNING *
                    """,
                    (
                        job_id,
                        environment_id,
                        session_id,
                        run_id,
                        row["project_id"],
                        engine,
                        runner_kind.value,
                        worker_profile,
                        task_class.value,
                        json.dumps(list(write_paths)),
                        branch_name,
                        idempotency_key, digest, new_uuid7(),
                        "PENDING" if runner_kind == RunnerKind.ACTIONS_HEADLESS else "NOT_REQUIRED",
                    ),
                ).fetchone()
                if created is None:
                    # ON CONFLICT waits for the winner; this separate READ COMMITTED
                    # statement sees its committed row, including a concurrent dispatch.
                    existing = conn.execute(
                        "SELECT * FROM amodex_environment_runner_jobs WHERE environment_id=%s AND idempotency_key=%s",
                        (environment_id, idempotency_key),
                    ).fetchone()
                    assert existing is not None
                    return _require_matching_intent(existing, digest)
        assert created is not None
        return _runner_job_from_row(created)

    def get_runner_job(self, job_id: str) -> RunnerJobRecord | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT * FROM amodex_environment_runner_jobs WHERE job_id=%s",
                (job_id,),
            ).fetchone()
        return _runner_job_from_row(row) if row else None

    def list_runner_jobs(
        self,
        *,
        environment_id: str | None = None,
        limit: int = 200,
    ) -> tuple[RunnerJobRecord, ...]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            if environment_id is None:
                rows = conn.execute(
                    "SELECT * FROM amodex_environment_runner_jobs ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM amodex_environment_runner_jobs
                    WHERE environment_id=%s
                    ORDER BY created_at DESC LIMIT %s
                    """,
                    (environment_id, limit),
                ).fetchall()
        return tuple(_runner_job_from_row(row) for row in rows)

    def begin_dispatch(self, *, job_id: str, target: dict[str, str]) -> RunnerJobRecord | None:
        """Commit one irreversible sending authorization, then release SQL locks.

        STARTED may already have been accepted externally. It is never reclaimable,
        even after process death. PENDING alone is safe to acquire for a first send.
        """
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                """UPDATE amodex_environment_runner_jobs j
                   SET dispatch_status='STARTED',dispatch_started_at=NOW(),dispatch_target=%s::jsonb
                   WHERE job_id=%s AND dispatch_status='PENDING' AND state='QUEUED'
                     AND EXISTS (
                         SELECT 1 FROM amodex_environments e JOIN amodex_sessions s
                           ON s.environment_id=e.environment_id
                         WHERE e.environment_id=j.environment_id AND s.session_id=j.session_id
                           AND e.status='ACTIVE' AND s.status='ACTIVE'
                     )
                   RETURNING j.*""", (json.dumps(target), job_id),
            ).fetchone()
        return _runner_job_from_row(row) if row else None

    def mark_dispatched(self, *, job_id: str, attempt_id: str) -> RunnerJobRecord:
        return self._finish_dispatch(job_id=job_id, attempt_id=attempt_id, confirmed=True)

    def mark_dispatch_unknown(self, *, job_id: str, attempt_id: str) -> RunnerJobRecord:
        return self._finish_dispatch(job_id=job_id, attempt_id=attempt_id, confirmed=False)

    def _finish_dispatch(self, *, job_id: str, attempt_id: str, confirmed: bool) -> RunnerJobRecord:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                row = conn.execute(
                    "SELECT * FROM amodex_environment_runner_jobs WHERE job_id=%s FOR UPDATE",
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(job_id)
                if str(row["dispatch_attempt_id"]) != attempt_id:
                    raise EnvironmentStateError("DISPATCH_ATTEMPT_MISMATCH", job_id)
                if row["dispatch_status"] != "STARTED":
                    return _runner_job_from_row(row)
                updated = conn.execute(
                    """
                    UPDATE amodex_environment_runner_jobs
                    SET dispatch_status=%s,dispatch_finished_at=NOW(),dispatch_error_code=%s,
                        state=CASE WHEN state='QUEUED' THEN %s ELSE state END,
                        dispatched_at=CASE WHEN %s THEN COALESCE(dispatched_at,NOW()) ELSE dispatched_at END,
                        summary=CASE WHEN state='QUEUED' AND NOT %s THEN 'Dispatch outcome unknown; do not resend' ELSE summary END,
                        updated_at=NOW()
                    WHERE job_id=%s
                    RETURNING *
                    """,
                    ("CONFIRMED" if confirmed else "UNKNOWN", None if confirmed else "EXTERNAL_OUTCOME_UNKNOWN",
                     "DISPATCHED" if confirmed else "DISPATCH_UNKNOWN", confirmed, confirmed, job_id),
                ).fetchone()
        assert updated is not None
        return _runner_job_from_row(updated)

    def claim_runner_job(self, *, job_id: str, github_run_id: str) -> dict[str, Any]:
        require_github_run_id(github_run_id)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                row = conn.execute(
                    "SELECT * FROM amodex_environment_runner_jobs WHERE job_id=%s FOR UPDATE",
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(job_id)
                state = RunnerJobState(row["state"])
                if state not in {
                    RunnerJobState.QUEUED,
                    RunnerJobState.DISPATCHED,
                    RunnerJobState.DISPATCH_UNKNOWN,
                }:
                    raise EnvironmentStateError("RUNNER_JOB_NOT_CLAIMABLE", f"{job_id}:{state.value}")
                row = conn.execute(
                    """
                    UPDATE amodex_environment_runner_jobs
                    SET state='CLAIMED', claimed_at=NOW(), updated_at=NOW(),
                        last_heartbeat_at=NOW(), lease_expires_at=NOW() + INTERVAL '2 minutes',
                        heartbeat_count=heartbeat_count + 1,
                        github_run_id=COALESCE(%s,github_run_id)
                    WHERE job_id=%s
                    RETURNING *
                    """,
                    (github_run_id, job_id),
                ).fetchone()
                assert row is not None
                context = conn.execute(
                    """
                    SELECT e.name AS environment_name,p.repo_url,r.prompt
                    FROM amodex_environment_runner_jobs j
                    JOIN amodex_environments e ON e.environment_id=j.environment_id
                    JOIN amodex_projects p ON p.project_id=j.project_id
                    JOIN amodex_runs r ON r.run_id=j.run_id
                    WHERE j.job_id=%s
                    """,
                    (job_id,),
                ).fetchone()
        assert context is not None
        job = _runner_job_from_row(row)
        return {
            "job": job,
            "environment_name": context["environment_name"],
            "repo_url": context["repo_url"],
            "objective": context["prompt"],
            "capabilities": capabilities_for_engine(job.engine),
        }

    def mark_running(self, *, job_id: str, github_run_id: str) -> RunnerJobRecord:
        require_github_run_id(github_run_id)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    UPDATE amodex_environment_runner_jobs
                    SET state='RUNNING', updated_at=NOW(),
                        last_heartbeat_at=NOW(), lease_expires_at=NOW() + INTERVAL '2 minutes',
                        heartbeat_count=heartbeat_count + 1
                    WHERE job_id=%s AND github_run_id=%s
                      AND state='CLAIMED' AND lease_expires_at > NOW()
                    RETURNING *
                    """,
                    (job_id, github_run_id),
                ).fetchone()
                if row is None:
                    exists = conn.execute(
                        "SELECT state,github_run_id FROM amodex_environment_runner_jobs WHERE job_id=%s",
                        (job_id,),
                    ).fetchone()
                    if exists is None:
                        raise KeyError(job_id)
                    if exists["github_run_id"] != github_run_id:
                        raise EnvironmentStateError("RUNNER_LEASE_OWNER_MISMATCH", job_id)
                    raise EnvironmentStateError(
                        "RUNNER_JOB_STATE_CONFLICT",
                        f"{job_id}:{exists['state']}->RUNNING",
                    )
        return _runner_job_from_row(row)

    def heartbeat_runner_job(
        self, *, job_id: str, github_run_id: str
    ) -> RunnerJobRecord:
        require_github_run_id(github_run_id)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    UPDATE amodex_environment_runner_jobs
                    SET last_heartbeat_at=NOW(),
                        lease_expires_at=NOW() + INTERVAL '2 minutes',
                        heartbeat_count=heartbeat_count + 1,
                        updated_at=NOW()
                    WHERE job_id=%s
                      AND github_run_id=%s
                      AND state IN ('CLAIMED','RUNNING')
                      AND lease_expires_at > NOW()
                    RETURNING *
                    """,
                    (job_id, github_run_id),
                ).fetchone()
                if row is None:
                    current = conn.execute(
                        "SELECT state,github_run_id,lease_expires_at FROM amodex_environment_runner_jobs WHERE job_id=%s",
                        (job_id,),
                    ).fetchone()
                    if current is None:
                        raise KeyError(job_id)
                    raise EnvironmentStateError(
                        "RUNNER_LEASE_LOST",
                        f"{job_id}: state={current['state']} owner_matches={current['github_run_id'] == github_run_id}",
                    )
        return _runner_job_from_row(row)

    def report_runner_job(
        self,
        *,
        job_id: str,
        state: RunnerJobState,
        summary: str,
        result_commit: str | None,
        github_run_id: str,
    ) -> RunnerJobRecord:
        require_github_run_id(github_run_id)
        if state not in {RunnerJobState.COMPLETED, RunnerJobState.FAILED, RunnerJobState.CANCELLED}:
            raise ValueError("terminal runner state required")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                row = conn.execute(
                    "SELECT state,github_run_id,lease_expires_at, (lease_expires_at <= NOW()) AS lease_expired FROM amodex_environment_runner_jobs WHERE job_id=%s FOR UPDATE",
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(job_id)
                current = RunnerJobState(row["state"])
                if current not in {
                    RunnerJobState.CLAIMED,
                    RunnerJobState.RUNNING,
                }:
                    raise EnvironmentStateError(
                        "RUNNER_JOB_TERMINAL_CONFLICT",
                        f"{job_id}:{current.value}",
                    )
                if row["github_run_id"] != github_run_id:
                    raise EnvironmentStateError("RUNNER_LEASE_OWNER_MISMATCH", job_id)
                if row["lease_expires_at"] is None:
                    raise EnvironmentStateError("RUNNER_LEASE_MISSING", job_id)
                if row["lease_expired"]:
                    raise EnvironmentStateError("RUNNER_LEASE_LOST", job_id)
                updated = conn.execute(
                    """
                    UPDATE amodex_environment_runner_jobs
                    SET state=%s,summary=%s,result_commit=%s,
                        github_run_id=COALESCE(%s,github_run_id),completed_at=NOW(),updated_at=NOW(),
                        lease_expires_at=NULL
                    WHERE job_id=%s
                    RETURNING *
                    """,
                    (state.value, summary, result_commit, github_run_id, job_id),
                ).fetchone()
        assert updated is not None
        return _runner_job_from_row(updated)

    def usage_projection(self, *, environment_id: str) -> UsageProjection:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            total = conn.execute(
                """
                SELECT COALESCE(SUM(input_tokens),0)::bigint AS input_tokens,
                       COALESCE(SUM(output_tokens),0)::bigint AS output_tokens,
                       COALESCE(SUM(requests),0)::bigint AS requests
                FROM amodex_environment_usage_events WHERE environment_id=%s
                """,
                (environment_id,),
            ).fetchone()
            rows = conn.execute(
                """
                SELECT provider,model,
                       COALESCE(SUM(input_tokens),0)::bigint AS input_tokens,
                       COALESCE(SUM(output_tokens),0)::bigint AS output_tokens,
                       COALESCE(SUM(requests),0)::bigint AS requests,
                       COUNT(*)::bigint AS events
                FROM amodex_environment_usage_events
                WHERE environment_id=%s
                GROUP BY provider,model
                ORDER BY provider,model
                """,
                (environment_id,),
            ).fetchall()
        assert total is not None
        return UsageProjection(
            environment_id=environment_id,
            input_tokens=int(total["input_tokens"]),
            output_tokens=int(total["output_tokens"]),
            requests=int(total["requests"]),
            by_provider=[
                {
                    "provider": row["provider"],
                    "model": row["model"],
                    "input_tokens": int(row["input_tokens"]),
                    "output_tokens": int(row["output_tokens"]),
                    "requests": int(row["requests"]),
                    "events": int(row["events"]),
                }
                for row in rows
            ],
        )


def _require_matching_intent(row: dict[str, Any], digest: str) -> RunnerJobRecord:
    if row["intent_digest"] != digest:
        raise EnvironmentStateError("IDEMPOTENCY_KEY_CONFLICT", "key already used with a different intent")
    return _runner_job_from_row(row)


def _runner_job_from_row(row: dict[str, Any]) -> RunnerJobRecord:
    paths = row["write_paths"]
    if isinstance(paths, str):
        paths = json.loads(paths)
    return RunnerJobRecord(
        job_id=str(row["job_id"]),
        environment_id=str(row["environment_id"]),
        session_id=str(row["session_id"]),
        run_id=str(row["run_id"]),
        project_id=str(row["project_id"]),
        engine=row["engine"],
        runner_kind=RunnerKind(row["runner_kind"]),
        worker_profile=row["worker_profile"],
        task_class=TaskBudgetClass(row["task_class"]),
        write_paths=tuple(paths or ()),
        state=RunnerJobState(row["state"]),
        branch_name=row["branch_name"],
        result_commit=row["result_commit"],
        summary=row["summary"],
        github_run_id=row["github_run_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        claimed_at=row["claimed_at"],
        completed_at=row["completed_at"],
        last_heartbeat_at=row.get("last_heartbeat_at"),
        lease_expires_at=row.get("lease_expires_at"),
        heartbeat_count=int(row.get("heartbeat_count") or 0),
        dispatch_attempt_id=str(row["dispatch_attempt_id"]) if row.get("dispatch_attempt_id") else None,
        dispatch_status=row.get("dispatch_status"),
        dispatch_target=row.get("dispatch_target"),
        dispatch_started_at=row.get("dispatch_started_at"),
        dispatch_finished_at=row.get("dispatch_finished_at"),
        dispatch_error_code=row.get("dispatch_error_code"),
    )
