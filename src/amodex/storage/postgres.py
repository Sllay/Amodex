"""PostgreSQL durable state/event repository for Stage 1."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from amodex.contracts.ids import new_uuid7
from amodex.runtime.models import EventKind, RunEvent, RunRecord, RunState


class StaleLeaseError(RuntimeError):
    pass


class PostgresRunRepository:
    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    def migrate(self, migration_path: str | Path) -> None:
        sql = Path(migration_path).read_text(encoding="utf-8")
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)

    def create_run(self, *, prompt: str) -> RunRecord:
        run_id = new_uuid7()
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO amodex_runs (run_id, prompt, state, event_seq) VALUES (%s, %s, %s, 1) RETURNING *", (run_id, prompt, RunState.QUEUED.value))
                    row = cur.fetchone(); assert row is not None
                    self._insert_event(cur, run_id=run_id, seq=1, kind=EventKind.RUN_CREATED, payload={"prompt_length": len(prompt)})
        return _run_from_row(row)

    def get_run(self, run_id: str) -> RunRecord | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM amodex_runs WHERE run_id = %s", (run_id,)); row = cur.fetchone()
        return _run_from_row(row) if row else None

    def list_events(self, run_id: str) -> tuple[RunEvent, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM amodex_run_events WHERE run_id = %s ORDER BY seq", (run_id,)); rows = cur.fetchall()
        return tuple(_event_from_row(row) for row in rows)

    def claim_next(self, *, worker_id: str, lease_seconds: int = 30) -> RunRecord | None:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT * FROM amodex_runs
                        WHERE state = %s OR (state IN (%s, %s) AND lease_expires_at IS NOT NULL AND lease_expires_at < NOW())
                        ORDER BY created_at, run_id FOR UPDATE SKIP LOCKED LIMIT 1
                    """, (RunState.QUEUED.value, RunState.CLAIMED.value, RunState.RUNNING.value))
                    row = cur.fetchone()
                    if row is None: return None
                    reclaimed = row["state"] != RunState.QUEUED.value
                    fence = int(row["fencing_token"]) + 1; seq = int(row["event_seq"]) + 1; rev = int(row["revision"]) + 1
                    cur.execute("""
                        UPDATE amodex_runs SET state=%s, revision=%s, event_seq=%s, fencing_token=%s,
                        lease_owner=%s, lease_expires_at=%s, updated_at=NOW() WHERE run_id=%s RETURNING *
                    """, (RunState.CLAIMED.value, rev, seq, fence, worker_id, expires_at, row["run_id"]))
                    updated = cur.fetchone(); assert updated is not None
                    self._insert_event(cur, run_id=str(row["run_id"]), seq=seq, kind=EventKind.LEASE_EXPIRED_RECLAIMED if reclaimed else EventKind.RUN_CLAIMED, payload={"worker_id":worker_id,"fencing_token":fence})
                    return _run_from_row(updated)

    def transition(self, *, run_id: str, worker_id: str, fencing_token: int, state: RunState, kind: EventKind,
                   payload: dict[str, Any] | None = None, provider: str | None = None, model: str | None = None,
                   result_text: str | None = None, last_error_code: str | None = None, release_lease: bool = False) -> RunRecord:
        payload = payload or {}
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM amodex_runs WHERE run_id=%s FOR UPDATE", (run_id,)); row = cur.fetchone()
                    if row is None: raise KeyError(run_id)
                    if row["lease_owner"] != worker_id or int(row["fencing_token"]) != fencing_token:
                        raise StaleLeaseError(f"stale worker lease for run {run_id}")
                    seq = int(row["event_seq"]) + 1; rev = int(row["revision"]) + 1
                    cur.execute("""
                        UPDATE amodex_runs SET state=%s, provider=COALESCE(%s,provider), model=COALESCE(%s,model),
                        result_text=COALESCE(%s,result_text), last_error_code=%s, revision=%s, event_seq=%s,
                        lease_owner=%s, lease_expires_at=%s, updated_at=NOW() WHERE run_id=%s RETURNING *
                    """, (state.value, provider, model, result_text, last_error_code, rev, seq,
                           None if release_lease else worker_id, None if release_lease else row["lease_expires_at"], run_id))
                    updated = cur.fetchone(); assert updated is not None
                    self._insert_event(cur, run_id=run_id, seq=seq, kind=kind, payload=payload)
                    return _run_from_row(updated)

    @staticmethod
    def _insert_event(cur: psycopg.Cursor[Any], *, run_id: str, seq: int, kind: EventKind, payload: dict[str, Any]) -> None:
        cur.execute("INSERT INTO amodex_run_events (event_id,run_id,seq,kind,payload) VALUES (%s,%s,%s,%s,%s::jsonb)",
                    (new_uuid7(), run_id, seq, kind.value, json.dumps(payload, sort_keys=True)))


def _run_from_row(row: dict[str, Any]) -> RunRecord:
    return RunRecord(run_id=str(row["run_id"]), prompt=row["prompt"], state=RunState(row["state"]), provider=row["provider"], model=row["model"], result_text=row["result_text"], last_error_code=row["last_error_code"], revision=int(row["revision"]), fencing_token=int(row["fencing_token"]), lease_owner=row["lease_owner"], lease_expires_at=row["lease_expires_at"], created_at=row["created_at"], updated_at=row["updated_at"])


def _event_from_row(row: dict[str, Any]) -> RunEvent:
    payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
    return RunEvent(event_id=str(row["event_id"]), run_id=str(row["run_id"]), seq=int(row["seq"]), kind=EventKind(row["kind"]), payload=payload, created_at=row["created_at"])
