"""Durable Stage 6 product/operations projections.

The repository exposes operator-facing state without becoming the authority for
DAG, provider routing, Git validation, or promotion. Canonical Stage 1/4/5 tables
are read as projections where useful.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from amodex.contracts.ids import new_uuid7
from amodex.product.models import (
    ApprovalRecord,
    ApprovalStatus,
    AuditEvent,
    CheckpointView,
    EvidenceView,
    ProjectRecord,
    ProjectStatus,
    ProviderConnection,
    RunControl,
    RunDesiredState,
    RunnerRecord,
    RunnerState,
    TaskView,
)
from amodex.runtime.models import EventKind, RunState


class ProductStateError(RuntimeError):
    pass


class PostgresProductRepository:
    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    @property
    def dsn(self) -> str:
        return self._dsn

    def migrate(self, migration_path: str | Path) -> None:
        sql = Path(migration_path).read_text(encoding="utf-8")
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)

    def ping(self) -> bool:
        try:
            with psycopg.connect(self._dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    return cur.fetchone() == (1,)
        except psycopg.Error:
            return False

    def schema_ready(self) -> bool:
        required = (
            "amodex_projects",
            "amodex_run_controls",
            "amodex_approvals",
            "amodex_provider_connections",
            "amodex_runners",
            "amodex_audit_events",
        )
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT relname FROM pg_class WHERE relname = ANY(%s)",
                    (list(required),),
                )
                found = {row[0] for row in cur.fetchall()}
        return found == set(required)

    def create_project(self, *, repo_url: str, name: str, actor: str = "owner") -> ProjectRecord:
        project_id = new_uuid7()
        now = datetime.now(timezone.utc)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO amodex_projects(project_id,name,repo_url,status,created_at,updated_at)
                            VALUES (%s,%s,%s,%s,%s,%s)
                            RETURNING *
                            """,
                            (project_id, name, repo_url, ProjectStatus.ACTIVE.value, now, now),
                        )
                        row = cur.fetchone()
                        assert row is not None
                        self._insert_audit(
                            cur,
                            actor=actor,
                            action="PROJECT_ADDED",
                            subject_type="project",
                            subject_id=project_id,
                            detail={"repo_url": repo_url, "name": name},
                        )
                        return _project_from_row(row)
            except psycopg.errors.UniqueViolation as exc:
                raise ProductStateError(f"project already exists for repository {repo_url}") from exc

    def get_project(self, project_id: str) -> ProjectRecord | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM amodex_projects WHERE project_id=%s", (project_id,))
                row = cur.fetchone()
        return _project_from_row(row) if row else None

    def list_projects(self) -> tuple[ProjectRecord, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM amodex_projects ORDER BY created_at, project_id")
                rows = cur.fetchall()
        return tuple(_project_from_row(row) for row in rows)

    def set_run_control(
        self,
        *,
        run_id: str,
        desired_state: RunDesiredState,
        reason: str | None,
        actor: str = "owner",
    ) -> RunControl:
        now = datetime.now(timezone.utc)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM amodex_runs WHERE run_id=%s FOR UPDATE", (run_id,))
                    run = cur.fetchone()
                    if run is None:
                        raise KeyError(run_id)
                    current = RunState(run["state"])
                    if current in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
                        raise ProductStateError(f"terminal run cannot change control state: {current.value}")

                    cur.execute(
                        """
                        INSERT INTO amodex_run_controls(run_id,desired_state,reason,requested_at,updated_at)
                        VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT (run_id) DO UPDATE
                        SET desired_state=EXCLUDED.desired_state,
                            reason=EXCLUDED.reason,
                            requested_at=EXCLUDED.requested_at,
                            updated_at=EXCLUDED.updated_at
                        RETURNING *
                        """,
                        (run_id, desired_state.value, reason, now, now),
                    )
                    control_row = cur.fetchone()
                    assert control_row is not None

                    event_kind = {
                        RunDesiredState.PAUSED: EventKind.RUN_PAUSE_REQUESTED,
                        RunDesiredState.ACTIVE: EventKind.RUN_RESUME_REQUESTED,
                        RunDesiredState.CANCELLED: EventKind.RUN_CANCEL_REQUESTED,
                    }[desired_state]
                    new_state: RunState | None = None
                    if desired_state == RunDesiredState.PAUSED and current in {
                        RunState.QUEUED,
                        RunState.WAITING_PROVIDER,
                    }:
                        new_state = RunState.PAUSED
                        event_kind = EventKind.RUN_PAUSED
                    elif desired_state == RunDesiredState.ACTIVE and current == RunState.PAUSED:
                        new_state = RunState.QUEUED
                        event_kind = EventKind.RUN_RESUMED
                    elif desired_state == RunDesiredState.CANCELLED and current in {
                        RunState.QUEUED,
                        RunState.WAITING_PROVIDER,
                        RunState.PAUSED,
                    }:
                        new_state = RunState.CANCELLED
                        event_kind = EventKind.RUN_CANCELLED

                    seq = int(run["event_seq"]) + 1
                    revision = int(run["revision"]) + 1
                    cur.execute(
                        """
                        UPDATE amodex_runs
                        SET state=COALESCE(%s,state), revision=%s, event_seq=%s, updated_at=NOW()
                        WHERE run_id=%s
                        """,
                        (new_state.value if new_state else None, revision, seq, run_id),
                    )
                    cur.execute(
                        """
                        INSERT INTO amodex_run_events(event_id,run_id,seq,kind,payload)
                        VALUES (%s,%s,%s,%s,%s::jsonb)
                        """,
                        (
                            new_uuid7(),
                            run_id,
                            seq,
                            event_kind.value,
                            json.dumps({"desired_state": desired_state.value, "reason": reason}, sort_keys=True),
                        ),
                    )
                    self._insert_audit(
                        cur,
                        actor=actor,
                        action=event_kind.value,
                        subject_type="run",
                        subject_id=run_id,
                        detail={"desired_state": desired_state.value, "reason": reason},
                    )
                    return _control_from_row(control_row)

    def get_run_control(self, run_id: str) -> RunControl | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM amodex_run_controls WHERE run_id=%s", (run_id,))
                row = cur.fetchone()
        return _control_from_row(row) if row else None

    def create_approval(
        self,
        *,
        action: str,
        subject_type: str,
        subject_id: str,
        impact: str,
        expires_at: datetime | None,
        actor: str = "system",
    ) -> ApprovalRecord:
        approval_id = new_uuid7()
        now = datetime.now(timezone.utc)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO amodex_approvals(
                            approval_id,action,subject_type,subject_id,impact,status,requested_at,expires_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
                        """,
                        (
                            approval_id,
                            action,
                            subject_type,
                            subject_id,
                            impact,
                            ApprovalStatus.PENDING.value,
                            now,
                            expires_at,
                        ),
                    )
                    row = cur.fetchone()
                    assert row is not None
                    self._insert_audit(
                        cur,
                        actor=actor,
                        action="APPROVAL_REQUESTED",
                        subject_type="approval",
                        subject_id=approval_id,
                        detail={"requested_action": action, "impact": impact, "subject_id": subject_id},
                    )
                    return _approval_from_row(row)

    def resolve_approval(
        self,
        *,
        approval_id: str,
        approve: bool,
        actor: str = "owner",
    ) -> ApprovalRecord:
        now = datetime.now(timezone.utc)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM amodex_approvals WHERE approval_id=%s FOR UPDATE", (approval_id,))
                    existing = cur.fetchone()
                    if existing is None:
                        raise KeyError(approval_id)
                    status = ApprovalStatus(existing["status"])
                    if status != ApprovalStatus.PENDING:
                        raise ProductStateError(f"approval is already {status.value}")
                    expires_at = existing["expires_at"]
                    if expires_at is not None and expires_at <= now:
                        next_status = ApprovalStatus.EXPIRED
                    else:
                        next_status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
                    cur.execute(
                        "UPDATE amodex_approvals SET status=%s,resolved_at=%s WHERE approval_id=%s RETURNING *",
                        (next_status.value, now, approval_id),
                    )
                    row = cur.fetchone()
                    assert row is not None
                    self._insert_audit(
                        cur,
                        actor=actor,
                        action=f"APPROVAL_{next_status.value}",
                        subject_type="approval",
                        subject_id=approval_id,
                        detail={"requested_action": existing["action"], "subject_id": existing["subject_id"]},
                    )
                    return _approval_from_row(row)

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM amodex_approvals WHERE approval_id=%s", (approval_id,))
                row = cur.fetchone()
        return _approval_from_row(row) if row else None

    def list_approvals(self, *, limit: int = 100) -> tuple[ApprovalRecord, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM amodex_approvals ORDER BY requested_at DESC LIMIT %s", (limit,))
                rows = cur.fetchall()
        return tuple(_approval_from_row(row) for row in rows)

    def upsert_provider_connection(
        self,
        *,
        provider: str,
        credential_pool_id: str,
        secret_ref: str,
        billing_class: str,
        enabled: bool,
        actor: str = "owner",
    ) -> dict[str, Any]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO amodex_provider_connections(
                            provider,credential_pool_id,secret_ref,billing_class,enabled,updated_at
                        ) VALUES (%s,%s,%s,%s,%s,NOW())
                        ON CONFLICT(provider) DO UPDATE SET
                            credential_pool_id=EXCLUDED.credential_pool_id,
                            secret_ref=EXCLUDED.secret_ref,
                            billing_class=EXCLUDED.billing_class,
                            enabled=EXCLUDED.enabled,
                            updated_at=NOW()
                        RETURNING *
                        """,
                        (provider, credential_pool_id, secret_ref, billing_class, enabled),
                    )
                    row = cur.fetchone()
                    assert row is not None
                    self._insert_audit(
                        cur,
                        actor=actor,
                        action="PROVIDER_CONNECTION_UPDATED",
                        subject_type="provider",
                        subject_id=provider,
                        detail={
                            "credential_pool_id": credential_pool_id,
                            "secret_ref": secret_ref,
                            "billing_class": billing_class,
                            "enabled": enabled,
                        },
                    )
                    return dict(row)

    def list_provider_connections_raw(self) -> tuple[dict[str, Any], ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM amodex_provider_connections ORDER BY provider")
                return tuple(dict(row) for row in cur.fetchall())

    def heartbeat_runner(
        self,
        *,
        runner_id: str,
        profile: str,
        state: RunnerState,
        capabilities: tuple[str, ...],
        detail: str | None,
    ) -> RunnerRecord:
        now = datetime.now(timezone.utc)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO amodex_runners(
                            runner_id,profile,state,capabilities,last_heartbeat_at,detail
                        ) VALUES (%s,%s,%s,%s::jsonb,%s,%s)
                        ON CONFLICT(runner_id) DO UPDATE SET
                            profile=EXCLUDED.profile,
                            state=EXCLUDED.state,
                            capabilities=EXCLUDED.capabilities,
                            last_heartbeat_at=EXCLUDED.last_heartbeat_at,
                            detail=EXCLUDED.detail
                        RETURNING *
                        """,
                        (runner_id, profile, state.value, json.dumps(capabilities), now, detail),
                    )
                    row = cur.fetchone()
                    assert row is not None
                    return _runner_from_row(row)

    def list_runners(self, *, stale_after_seconds: int = 120) -> tuple[RunnerRecord, ...]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM amodex_runners ORDER BY last_heartbeat_at DESC")
                rows = cur.fetchall()
        result: list[RunnerRecord] = []
        for row in rows:
            if row["last_heartbeat_at"] < cutoff and row["state"] in {
                RunnerState.STARTING.value,
                RunnerState.RUNNING.value,
                RunnerState.DEGRADED.value,
            }:
                row = dict(row)
                row["state"] = RunnerState.OFFLINE.value
            result.append(_runner_from_row(row))
        return tuple(result)

    def upsert_task_view(self, task: TaskView) -> None:
        payload = task.model_dump(mode="json")
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO amodex_task_views(run_id,task_id,objective,state,dependencies,worker_profile,updated_at)
                    VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s)
                    ON CONFLICT(run_id,task_id) DO UPDATE SET
                        objective=EXCLUDED.objective,state=EXCLUDED.state,
                        dependencies=EXCLUDED.dependencies,worker_profile=EXCLUDED.worker_profile,
                        updated_at=EXCLUDED.updated_at
                    """,
                    (
                        payload["run_id"], payload["task_id"], payload["objective"], payload["state"],
                        json.dumps(payload["dependencies"]), payload["worker_profile"], task.updated_at,
                    ),
                )

    def list_task_views(self, *, run_id: str | None = None) -> tuple[TaskView, ...]:
        query = "SELECT * FROM amodex_task_views"
        args: tuple[Any, ...] = ()
        if run_id is not None:
            query += " WHERE run_id=%s"
            args = (run_id,)
        query += " ORDER BY run_id, task_id"
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(query, args)
                rows = cur.fetchall()
        return tuple(_task_from_row(row) for row in rows)

    def upsert_evidence_view(self, evidence: EvidenceView) -> None:
        payload = evidence.model_dump(mode="json")
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO amodex_evidence_views(
                        evidence_id,run_id,task_id,subject_sha,validator_id,result,summary,artifact_sha256,observed_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                    ON CONFLICT(evidence_id) DO UPDATE SET
                        run_id=EXCLUDED.run_id,task_id=EXCLUDED.task_id,subject_sha=EXCLUDED.subject_sha,
                        validator_id=EXCLUDED.validator_id,result=EXCLUDED.result,summary=EXCLUDED.summary,
                        artifact_sha256=EXCLUDED.artifact_sha256,observed_at=EXCLUDED.observed_at
                    """,
                    (
                        payload["evidence_id"], payload["run_id"], payload["task_id"], payload["subject_sha"],
                        payload["validator_id"], payload["result"], payload["summary"],
                        json.dumps(payload["artifact_sha256"]), evidence.observed_at,
                    ),
                )

    def list_evidence_views(self, *, limit: int = 200) -> tuple[EvidenceView, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM amodex_evidence_views ORDER BY observed_at DESC LIMIT %s", (limit,))
                rows = cur.fetchall()
        return tuple(_evidence_from_row(row) for row in rows)

    def upsert_checkpoint_view(self, checkpoint: CheckpointView) -> None:
        payload = checkpoint.model_dump(mode="json")
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO amodex_checkpoint_views(
                        checkpoint_id,run_id,task_id,checkpoint_class,commit_sha,evidence_ids,created_at
                    ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)
                    ON CONFLICT(checkpoint_id) DO UPDATE SET
                        run_id=EXCLUDED.run_id,task_id=EXCLUDED.task_id,
                        checkpoint_class=EXCLUDED.checkpoint_class,commit_sha=EXCLUDED.commit_sha,
                        evidence_ids=EXCLUDED.evidence_ids,created_at=EXCLUDED.created_at
                    """,
                    (
                        payload["checkpoint_id"], payload["run_id"], payload["task_id"],
                        payload["checkpoint_class"], payload["commit_sha"],
                        json.dumps(payload["evidence_ids"]), checkpoint.created_at,
                    ),
                )

    def list_checkpoint_views(self, *, limit: int = 200) -> tuple[CheckpointView, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM amodex_checkpoint_views ORDER BY created_at DESC LIMIT %s", (limit,))
                rows = cur.fetchall()
        return tuple(_checkpoint_from_row(row) for row in rows)

    def list_audit(self, *, limit: int = 200) -> tuple[AuditEvent, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM amodex_audit_events ORDER BY created_at DESC LIMIT %s", (limit,))
                rows = cur.fetchall()
        return tuple(_audit_from_row(row) for row in rows)

    def append_audit(
        self,
        *,
        actor: str,
        action: str,
        subject_type: str,
        subject_id: str,
        detail: dict[str, Any] | None = None,
    ) -> AuditEvent:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                row = self._insert_audit(
                    cur,
                    actor=actor,
                    action=action,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    detail=detail or {},
                )
                return _audit_from_row(row)

    def settings(self) -> dict[str, Any]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT key,value FROM amodex_product_settings ORDER BY key")
                rows = cur.fetchall()
        return {row["key"]: _json(row["value"]) for row in rows}

    def provider_status_rows(self) -> tuple[dict[str, Any], ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (provider,credential_pool_id)
                        provider,credential_pool_id,billing_class,health,quota_available,
                        free_capacity_ratio,observed_at,reason
                    FROM amodex_provider_availability_observations
                    ORDER BY provider,credential_pool_id,observed_at DESC
                    """
                )
                return tuple(dict(row) for row in cur.fetchall())

    def candidate_rows(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        return self._safe_rows(
            "SELECT * FROM amodex_candidates ORDER BY created_at DESC LIMIT %s",
            (limit,),
        )

    def promotion_rows(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        return self._safe_rows(
            "SELECT * FROM amodex_promotions ORDER BY created_at DESC LIMIT %s",
            (limit,),
        )

    def retention_preview(self, *, terminal_run_days: int) -> dict[str, int]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=terminal_run_days)
        terminal = (
            RunState.COMPLETED.value,
            RunState.FAILED.value,
            RunState.CANCELLED.value,
            RunState.BLOCKED.value,
            RunState.UNKNOWN_OUTCOME.value,
        )
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM amodex_runs WHERE state = ANY(%s) AND updated_at < %s",
                    (list(terminal), cutoff),
                )
                run_count = int(cur.fetchone()[0])
                cur.execute(
                    """
                    SELECT COUNT(*) FROM amodex_run_events e
                    JOIN amodex_runs r ON r.run_id=e.run_id
                    WHERE r.state = ANY(%s) AND r.updated_at < %s
                    """,
                    (list(terminal), cutoff),
                )
                event_count = int(cur.fetchone()[0])
        return {"runs": run_count, "events": event_count}

    def _safe_rows(self, query: str, args: tuple[Any, ...]) -> tuple[dict[str, Any], ...]:
        try:
            with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(query, args)
                    return tuple(dict(row) for row in cur.fetchall())
        except psycopg.errors.UndefinedTable:
            return ()

    @staticmethod
    def _insert_audit(
        cur: psycopg.Cursor[Any],
        *,
        actor: str,
        action: str,
        subject_type: str,
        subject_id: str,
        detail: dict[str, Any],
    ) -> dict[str, Any]:
        cur.execute(
            """
            INSERT INTO amodex_audit_events(audit_id,actor,action,subject_type,subject_id,detail)
            VALUES (%s,%s,%s,%s,%s,%s::jsonb)
            RETURNING *
            """,
            (new_uuid7(), actor, action, subject_type, subject_id, json.dumps(detail, sort_keys=True)),
        )
        row = cur.fetchone()
        assert row is not None
        return dict(row)


def _json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _project_from_row(row: dict[str, Any]) -> ProjectRecord:
    return ProjectRecord(
        project_id=str(row["project_id"]), name=row["name"], repo_url=row["repo_url"],
        status=ProjectStatus(row["status"]), created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _control_from_row(row: dict[str, Any]) -> RunControl:
    return RunControl(
        run_id=str(row["run_id"]), desired_state=RunDesiredState(row["desired_state"]),
        reason=row["reason"], requested_at=row["requested_at"], updated_at=row["updated_at"],
    )


def _approval_from_row(row: dict[str, Any]) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=str(row["approval_id"]), action=row["action"], subject_type=row["subject_type"],
        subject_id=row["subject_id"], impact=row["impact"], status=ApprovalStatus(row["status"]),
        requested_at=row["requested_at"], resolved_at=row["resolved_at"], expires_at=row["expires_at"],
    )


def _runner_from_row(row: dict[str, Any]) -> RunnerRecord:
    return RunnerRecord(
        runner_id=row["runner_id"], profile=row["profile"], state=RunnerState(row["state"]),
        capabilities=tuple(_json(row["capabilities"])), last_heartbeat_at=row["last_heartbeat_at"], detail=row["detail"],
    )


def _task_from_row(row: dict[str, Any]) -> TaskView:
    return TaskView(
        run_id=str(row["run_id"]), task_id=row["task_id"], objective=row["objective"], state=row["state"],
        dependencies=tuple(_json(row["dependencies"])), worker_profile=row["worker_profile"], updated_at=row["updated_at"],
    )


def _evidence_from_row(row: dict[str, Any]) -> EvidenceView:
    return EvidenceView(
        evidence_id=row["evidence_id"], run_id=str(row["run_id"]) if row["run_id"] else None,
        task_id=row["task_id"], subject_sha=row["subject_sha"], validator_id=row["validator_id"],
        result=row["result"], summary=row["summary"], artifact_sha256=tuple(_json(row["artifact_sha256"])),
        observed_at=row["observed_at"],
    )


def _checkpoint_from_row(row: dict[str, Any]) -> CheckpointView:
    return CheckpointView(
        checkpoint_id=row["checkpoint_id"], run_id=str(row["run_id"]) if row["run_id"] else None,
        task_id=row["task_id"], checkpoint_class=row["checkpoint_class"], commit_sha=row["commit_sha"],
        evidence_ids=tuple(_json(row["evidence_ids"])), created_at=row["created_at"],
    )


def _audit_from_row(row: dict[str, Any]) -> AuditEvent:
    return AuditEvent(
        audit_id=str(row["audit_id"]), actor=row["actor"], action=row["action"],
        subject_type=row["subject_type"], subject_id=row["subject_id"], detail=_json(row["detail"]),
        created_at=row["created_at"],
    )
