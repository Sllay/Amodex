"""PostgreSQL durable state/event repository for Amodex runs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from amodex.contracts.ids import new_uuid7
from amodex.runtime.models import (
    EventKind,
    ProviderRequestState,
    ProviderResponse,
    RunEvent,
    RunRecord,
    RunState,
)


class StaleLeaseError(RuntimeError):
    pass


class ProviderIntentError(RuntimeError):
    pass


class PostgresRunRepository:
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

    def migrate_all(self, migrations_dir: str | Path) -> tuple[str, ...]:
        root = Path(migrations_dir)
        files = tuple(sorted(root.glob("*.sql")))
        if not files:
            raise ValueError(f"no SQL migrations found in {root}")
        applied: list[str] = []
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS amodex_schema_migrations (
                        version TEXT PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            conn.commit()
            for path in files:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT 1 FROM amodex_schema_migrations WHERE version=%s",
                            (path.name,),
                        )
                        if cur.fetchone() is not None:
                            continue
                        cur.execute(path.read_text(encoding="utf-8"))
                        cur.execute(
                            "INSERT INTO amodex_schema_migrations (version) VALUES (%s)",
                            (path.name,),
                        )
                        applied.append(path.name)
        return tuple(applied)

    def create_run(self, *, prompt: str, project_id: str | None = None) -> RunRecord:
        run_id = new_uuid7()
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    project_column = self._column_exists(cur, table="amodex_runs", column="project_id")
                    if project_id is not None and not project_column:
                        raise ValueError("project-aware runs require the Stage 6 migration")
                    if project_column:
                        cur.execute(
                            """
                            INSERT INTO amodex_runs (run_id, project_id, prompt, state, event_seq)
                            VALUES (%s, %s, %s, %s, 1)
                            RETURNING *
                            """,
                            (run_id, project_id, prompt, RunState.QUEUED.value),
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO amodex_runs (run_id, prompt, state, event_seq)
                            VALUES (%s, %s, %s, 1)
                            RETURNING *
                            """,
                            (run_id, prompt, RunState.QUEUED.value),
                        )
                    row = cur.fetchone()
                    assert row is not None
                    self._insert_event(
                        cur,
                        run_id=run_id,
                        seq=1,
                        kind=EventKind.RUN_CREATED,
                        payload={"prompt_length": len(prompt), "project_id": project_id},
                    )
        return _run_from_row(row)

    def get_run(self, run_id: str) -> RunRecord | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM amodex_runs WHERE run_id = %s", (run_id,))
                row = cur.fetchone()
        return _run_from_row(row) if row else None

    def list_runs(self, *, project_id: str | None = None, limit: int = 100) -> tuple[RunRecord, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                if project_id is not None and self._column_exists(cur, table="amodex_runs", column="project_id"):
                    cur.execute(
                        "SELECT * FROM amodex_runs WHERE project_id=%s ORDER BY created_at DESC LIMIT %s",
                        (project_id, limit),
                    )
                else:
                    cur.execute("SELECT * FROM amodex_runs ORDER BY created_at DESC LIMIT %s", (limit,))
                rows = cur.fetchall()
        return tuple(_run_from_row(row) for row in rows)

    def list_events(self, run_id: str) -> tuple[RunEvent, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM amodex_run_events WHERE run_id = %s ORDER BY seq",
                    (run_id,),
                )
                rows = cur.fetchall()
        return tuple(_event_from_row(row) for row in rows)

    def get_run_control_state(self, run_id: str) -> str:
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                if not self._table_exists(cur, "amodex_run_controls"):
                    return "ACTIVE"
                cur.execute("SELECT desired_state FROM amodex_run_controls WHERE run_id=%s", (run_id,))
                row = cur.fetchone()
        return str(row[0]) if row else "ACTIVE"

    def claim_next(self, *, worker_id: str, lease_seconds: int = 30) -> RunRecord | None:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    controls = self._table_exists(cur, "amodex_run_controls")
                    control_filter = """
                        AND NOT EXISTS (
                            SELECT 1 FROM amodex_run_controls c
                            WHERE c.run_id = amodex_runs.run_id
                              AND c.desired_state IN ('PAUSED','CANCELLED')
                        )
                    """ if controls else ""
                    cur.execute(
                        f"""
                        SELECT * FROM amodex_runs
                        WHERE (
                            state = %s
                            OR (
                                state IN (%s, %s)
                                AND lease_expires_at IS NOT NULL
                                AND lease_expires_at < NOW()
                            )
                        )
                        {control_filter}
                        ORDER BY created_at, run_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                        """,
                        (
                            RunState.QUEUED.value,
                            RunState.CLAIMED.value,
                            RunState.RUNNING.value,
                        ),
                    )
                    row = cur.fetchone()
                    if row is None:
                        return None
                    reclaimed = row["state"] != RunState.QUEUED.value
                    fence = int(row["fencing_token"]) + 1
                    seq = int(row["event_seq"]) + 1
                    rev = int(row["revision"]) + 1
                    cur.execute(
                        """
                        UPDATE amodex_runs
                        SET state=%s,
                            revision=%s,
                            event_seq=%s,
                            fencing_token=%s,
                            lease_owner=%s,
                            lease_expires_at=%s,
                            updated_at=NOW()
                        WHERE run_id=%s
                        RETURNING *
                        """,
                        (
                            RunState.CLAIMED.value,
                            rev,
                            seq,
                            fence,
                            worker_id,
                            expires_at,
                            row["run_id"],
                        ),
                    )
                    updated = cur.fetchone()
                    assert updated is not None
                    self._insert_event(
                        cur,
                        run_id=str(row["run_id"]),
                        seq=seq,
                        kind=(
                            EventKind.LEASE_EXPIRED_RECLAIMED
                            if reclaimed
                            else EventKind.RUN_CLAIMED
                        ),
                        payload={"worker_id": worker_id, "fencing_token": fence},
                    )
                    return _run_from_row(updated)

    def transition(
        self,
        *,
        run_id: str,
        worker_id: str,
        fencing_token: int,
        state: RunState,
        kind: EventKind,
        payload: dict[str, Any] | None = None,
        provider: str | None = None,
        model: str | None = None,
        result_text: str | None = None,
        last_error_code: str | None = None,
        release_lease: bool = False,
    ) -> RunRecord:
        payload = payload or {}
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    row = self._lock_owned_run(
                        cur,
                        run_id=run_id,
                        worker_id=worker_id,
                        fencing_token=fencing_token,
                    )
                    seq = int(row["event_seq"]) + 1
                    rev = int(row["revision"]) + 1
                    cur.execute(
                        """
                        UPDATE amodex_runs
                        SET state=%s,
                            provider=COALESCE(%s,provider),
                            model=COALESCE(%s,model),
                            result_text=COALESCE(%s,result_text),
                            last_error_code=%s,
                            revision=%s,
                            event_seq=%s,
                            lease_owner=%s,
                            lease_expires_at=%s,
                            updated_at=NOW()
                        WHERE run_id=%s
                        RETURNING *
                        """,
                        (
                            state.value,
                            provider,
                            model,
                            result_text,
                            last_error_code,
                            rev,
                            seq,
                            None if release_lease else worker_id,
                            None if release_lease else row["lease_expires_at"],
                            run_id,
                        ),
                    )
                    updated = cur.fetchone()
                    assert updated is not None
                    self._insert_event(
                        cur,
                        run_id=run_id,
                        seq=seq,
                        kind=kind,
                        payload=payload,
                    )
                    return _run_from_row(updated)

    def mark_provider_request_started(
        self,
        *,
        run_id: str,
        worker_id: str,
        fencing_token: int,
        request_key: str,
        provider: str,
        model: str,
    ) -> RunRecord:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    row = self._lock_owned_run(
                        cur,
                        run_id=run_id,
                        worker_id=worker_id,
                        fencing_token=fencing_token,
                    )
                    current = ProviderRequestState(
                        row.get("provider_request_state") or ProviderRequestState.NONE.value
                    )
                    if current != ProviderRequestState.NONE:
                        raise ProviderIntentError(
                            f"provider request already {current.value} for run {run_id}"
                        )
                    seq = int(row["event_seq"]) + 1
                    rev = int(row["revision"]) + 1
                    cur.execute(
                        """
                        UPDATE amodex_runs
                        SET state=%s,
                            provider=%s,
                            model=%s,
                            provider_request_key=%s,
                            provider_request_state=%s,
                            revision=%s,
                            event_seq=%s,
                            updated_at=NOW()
                        WHERE run_id=%s
                        RETURNING *
                        """,
                        (
                            RunState.RUNNING.value,
                            provider,
                            model,
                            request_key,
                            ProviderRequestState.STARTED.value,
                            rev,
                            seq,
                            run_id,
                        ),
                    )
                    updated = cur.fetchone()
                    assert updated is not None
                    self._insert_event(
                        cur,
                        run_id=run_id,
                        seq=seq,
                        kind=EventKind.PROVIDER_REQUEST_STARTED,
                        payload={"request_key": request_key},
                    )
                    return _run_from_row(updated)

    def mark_provider_request_completed(
        self,
        *,
        run_id: str,
        worker_id: str,
        fencing_token: int,
        request_key: str,
        response: ProviderResponse,
    ) -> RunRecord:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    row = self._lock_owned_run(
                        cur,
                        run_id=run_id,
                        worker_id=worker_id,
                        fencing_token=fencing_token,
                    )
                    current = ProviderRequestState(
                        row.get("provider_request_state") or ProviderRequestState.NONE.value
                    )
                    stored_key = (
                        str(row["provider_request_key"])
                        if row.get("provider_request_key") is not None
                        else None
                    )
                    if current != ProviderRequestState.STARTED or stored_key != request_key:
                        raise ProviderIntentError(
                            f"provider completion does not match durable intent for run {run_id}"
                        )
                    seq = int(row["event_seq"]) + 1
                    rev = int(row["revision"]) + 1
                    cur.execute(
                        """
                        UPDATE amodex_runs
                        SET provider=%s,
                            model=%s,
                            result_text=%s,
                            provider_request_state=%s,
                            revision=%s,
                            event_seq=%s,
                            updated_at=NOW()
                        WHERE run_id=%s
                        RETURNING *
                        """,
                        (
                            response.provider,
                            response.model,
                            response.text,
                            ProviderRequestState.COMPLETED.value,
                            rev,
                            seq,
                            run_id,
                        ),
                    )
                    updated = cur.fetchone()
                    assert updated is not None
                    self._insert_event(
                        cur,
                        run_id=run_id,
                        seq=seq,
                        kind=EventKind.PROVIDER_REQUEST_COMPLETED,
                        payload={"usage": response.usage.model_dump(mode="json")},
                    )
                    return _run_from_row(updated)

    @staticmethod
    def _column_exists(cur: psycopg.Cursor[Any], *, table: str, column: str) -> bool:
        cur.execute(
            """
            SELECT EXISTS(
                SELECT 1 FROM information_schema.columns
                WHERE table_schema='public' AND table_name=%s AND column_name=%s
            ) AS present
            """,
            (table, column),
        )
        return _scalar_bool(cur.fetchone())

    @staticmethod
    def _table_exists(cur: psycopg.Cursor[Any], table: str) -> bool:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (f"public.{table}",))
        return _scalar_bool(cur.fetchone())

    @staticmethod
    def _lock_owned_run(
        cur: psycopg.Cursor[Any],
        *,
        run_id: str,
        worker_id: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        cur.execute("SELECT * FROM amodex_runs WHERE run_id=%s FOR UPDATE", (run_id,))
        row = cur.fetchone()
        if row is None:
            raise KeyError(run_id)
        if row["lease_owner"] != worker_id or int(row["fencing_token"]) != fencing_token:
            raise StaleLeaseError(f"stale worker lease for run {run_id}")
        return row

    @staticmethod
    def _insert_event(
        cur: psycopg.Cursor[Any],
        *,
        run_id: str,
        seq: int,
        kind: EventKind,
        payload: dict[str, Any],
    ) -> None:
        cur.execute(
            """
            INSERT INTO amodex_run_events (event_id,run_id,seq,kind,payload)
            VALUES (%s,%s,%s,%s,%s::jsonb)
            """,
            (new_uuid7(), run_id, seq, kind.value, json.dumps(payload, sort_keys=True)),
        )


def _scalar_bool(row: Any) -> bool:
    if row is None:
        return False
    if isinstance(row, dict):
        return bool(row["present"])
    return bool(row[0])


def _run_from_row(row: dict[str, Any]) -> RunRecord:
    raw_request_key = row.get("provider_request_key")
    raw_request_state = row.get("provider_request_state") or ProviderRequestState.NONE.value
    raw_project_id = row.get("project_id")
    return RunRecord(
        run_id=str(row["run_id"]),
        project_id=str(raw_project_id) if raw_project_id is not None else None,
        prompt=row["prompt"],
        state=RunState(row["state"]),
        provider=row["provider"],
        model=row["model"],
        result_text=row["result_text"],
        last_error_code=row["last_error_code"],
        provider_request_key=str(raw_request_key) if raw_request_key is not None else None,
        provider_request_state=ProviderRequestState(raw_request_state),
        revision=int(row["revision"]),
        fencing_token=int(row["fencing_token"]),
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _event_from_row(row: dict[str, Any]) -> RunEvent:
    payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
    return RunEvent(
        event_id=str(row["event_id"]),
        run_id=str(row["run_id"]),
        seq=int(row["seq"]),
        kind=EventKind(row["kind"]),
        payload=payload,
        created_at=row["created_at"],
    )
