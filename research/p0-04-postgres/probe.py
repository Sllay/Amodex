#!/usr/bin/env python3
"""P0-04: prove PostgreSQL-only durable claim/lease/fencing semantics.

This is research code, not the production persistence layer. It intentionally
uses multiple OS processes against one real PostgreSQL instance and exercises
stale-worker rejection plus crash rollback/reconciliation.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import queue
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row


class ProbeFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeFailure(message)


@dataclass(frozen=True)
class Claim:
    job_id: str
    worker_id: str
    fencing_token: int
    lease_expires_at: str


SCHEMA_SQL = """
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS jobs;

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    fencing_token BIGINT NOT NULL DEFAULT 0,
    protected_value TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE events (
    id BIGSERIAL PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    fencing_token BIGINT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX events_job_id_idx ON events(job_id, id);
"""


def connect(dsn: str):
    return psycopg.connect(dsn, row_factory=dict_row)


def reset_database(dsn: str) -> None:
    with connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(
                "INSERT INTO jobs(id, state, payload) VALUES (%s, 'QUEUED', %s::jsonb)",
                ("claim-job", json.dumps({"kind": "claim-race"})),
            )


def claim_job(dsn: str, worker_id: str, lease_seconds: int = 6) -> Claim | None:
    with connect(dsn) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id
                    FROM jobs
                    WHERE state = 'QUEUED'
                       OR (state = 'RUNNING' AND lease_expires_at <= clock_timestamp())
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
                if row is None:
                    return None
                job_id = row["id"]
                cur.execute(
                    """
                    UPDATE jobs
                    SET state = 'RUNNING',
                        lease_owner = %s,
                        lease_expires_at = clock_timestamp() + (%s * interval '1 second'),
                        fencing_token = fencing_token + 1,
                        updated_at = clock_timestamp()
                    WHERE id = %s
                    RETURNING fencing_token, lease_expires_at
                    """,
                    (worker_id, lease_seconds, job_id),
                )
                claimed = cur.fetchone()
                require(claimed is not None, "claim update returned no row")
                token = int(claimed["fencing_token"])
                cur.execute(
                    """
                    INSERT INTO events(job_id, event_type, actor, fencing_token, payload)
                    VALUES (%s, 'CLAIMED', %s, %s, %s::jsonb)
                    """,
                    (job_id, worker_id, token, json.dumps({"lease_seconds": lease_seconds})),
                )
                return Claim(
                    job_id=job_id,
                    worker_id=worker_id,
                    fencing_token=token,
                    lease_expires_at=claimed["lease_expires_at"].isoformat(),
                )


def claim_worker(dsn: str, worker_id: str, start_event, result_queue) -> None:
    start_event.wait(timeout=10)
    try:
        claim = claim_job(dsn, worker_id)
        result_queue.put({"worker": worker_id, "claim": asdict(claim) if claim else None})
    except BaseException as exc:
        result_queue.put({"worker": worker_id, "error": repr(exc)})


def heartbeat(dsn: str, claim: Claim, lease_seconds: int = 8) -> bool:
    with connect(dsn) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE jobs
                    SET lease_expires_at = clock_timestamp() + (%s * interval '1 second'),
                        updated_at = clock_timestamp()
                    WHERE id = %s
                      AND state = 'RUNNING'
                      AND lease_owner = %s
                      AND fencing_token = %s
                      AND lease_expires_at > clock_timestamp()
                    RETURNING lease_expires_at
                    """,
                    (lease_seconds, claim.job_id, claim.worker_id, claim.fencing_token),
                )
                row = cur.fetchone()
                if row is None:
                    return False
                cur.execute(
                    """
                    INSERT INTO events(job_id, event_type, actor, fencing_token, payload)
                    VALUES (%s, 'HEARTBEAT', %s, %s, %s::jsonb)
                    """,
                    (
                        claim.job_id,
                        claim.worker_id,
                        claim.fencing_token,
                        json.dumps({"lease_seconds": lease_seconds}),
                    ),
                )
                return True


def force_expire(dsn: str, job_id: str) -> None:
    with connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET lease_expires_at = clock_timestamp() - interval '1 second',
                    updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (job_id,),
            )


def protected_update(dsn: str, claim: Claim, value: str) -> bool:
    with connect(dsn) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE jobs
                    SET protected_value = %s,
                        updated_at = clock_timestamp()
                    WHERE id = %s
                      AND state = 'RUNNING'
                      AND lease_owner = %s
                      AND fencing_token = %s
                      AND lease_expires_at > clock_timestamp()
                    """,
                    (value, claim.job_id, claim.worker_id, claim.fencing_token),
                )
                return cur.rowcount == 1


def transactional_transition(
    dsn: str,
    claim: Claim,
    *,
    expected_state: str,
    new_state: str,
    event_type: str,
    fail_before_commit: bool = False,
) -> bool:
    try:
        with connect(dsn) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE jobs
                        SET state = %s, updated_at = clock_timestamp()
                        WHERE id = %s
                          AND state = %s
                          AND lease_owner = %s
                          AND fencing_token = %s
                        """,
                        (
                            new_state,
                            claim.job_id,
                            expected_state,
                            claim.worker_id,
                            claim.fencing_token,
                        ),
                    )
                    if cur.rowcount != 1:
                        return False
                    cur.execute(
                        """
                        INSERT INTO events(job_id, event_type, actor, fencing_token, payload)
                        VALUES (%s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            claim.job_id,
                            event_type,
                            claim.worker_id,
                            claim.fencing_token,
                            json.dumps({"from": expected_state, "to": new_state}),
                        ),
                    )
                    if fail_before_commit:
                        raise RuntimeError("intentional rollback after state+event")
        return True
    except RuntimeError as exc:
        if "intentional rollback" not in str(exc):
            raise
        return False


def seed_kill_job(dsn: str) -> Claim:
    with connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs(
                    id, state, lease_owner, lease_expires_at, fencing_token, payload
                )
                VALUES (
                    'kill-job', 'RUNNING', 'worker-killed',
                    clock_timestamp() + interval '30 seconds', 1,
                    %s::jsonb
                )
                """,
                (json.dumps({"kind": "kill-during-transition"}),),
            )
    return Claim("kill-job", "worker-killed", 1, "seeded")


def uncommitted_transition_worker(dsn: str, ready_queue) -> None:
    conn = connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE jobs
            SET state = 'UNCOMMITTED', updated_at = clock_timestamp()
            WHERE id = 'kill-job'
              AND lease_owner = 'worker-killed'
              AND fencing_token = 1
            """
        )
        if cur.rowcount != 1:
            ready_queue.put({"ready": False, "reason": "update rejected"})
            return
        cur.execute(
            """
            INSERT INTO events(job_id, event_type, actor, fencing_token, payload)
            VALUES ('kill-job', 'UNCOMMITTED_EVENT', 'worker-killed', 1, '{}'::jsonb)
            """
        )
        ready_queue.put({"ready": True})
        time.sleep(60)
        conn.commit()
    finally:
        conn.close()


def fetch_job(dsn: str, job_id: str) -> dict[str, Any]:
    with connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
            require(row is not None, f"job missing: {job_id}")
            return dict(row)


def event_count(dsn: str, job_id: str, event_type: str) -> int:
    with connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS count FROM events WHERE job_id = %s AND event_type = %s",
                (job_id, event_type),
            )
            return int(cur.fetchone()["count"])


def database_observation(dsn: str) -> dict[str, Any]:
    with connect(dsn) as conn:
        with conn.cursor() as cur:
            settings: dict[str, str] = {}
            for name in (
                "server_version",
                "shared_buffers",
                "work_mem",
                "maintenance_work_mem",
                "max_connections",
            ):
                cur.execute("SELECT current_setting(%s) AS value", (name,))
                settings[name] = str(cur.fetchone()["value"])
            cur.execute(
                "SELECT count(*) AS count FROM pg_stat_activity WHERE datname = current_database()"
            )
            connections = int(cur.fetchone()["count"])
            cur.execute(
                "SELECT count(*) AS count FROM pg_locks WHERE locktype = 'advisory'"
            )
            advisory_locks = int(cur.fetchone()["count"])
            cur.execute("SELECT pg_database_size(current_database()) AS bytes")
            database_bytes = int(cur.fetchone()["bytes"])
    return {
        "settings": settings,
        "observed_connections": connections,
        "advisory_lock_count": advisory_locks,
        "database_bytes": database_bytes,
    }


def run_probe(dsn: str) -> dict[str, Any]:
    reset_database(dsn)

    ctx = mp.get_context("spawn")
    start_event = ctx.Event()
    result_queue = ctx.Queue()
    workers = [
        ctx.Process(
            target=claim_worker,
            args=(dsn, f"worker-{index}", start_event, result_queue),
        )
        for index in range(4)
    ]
    for proc in workers:
        proc.start()
    start_event.set()

    race_results: list[dict[str, Any]] = []
    for _ in workers:
        try:
            race_results.append(result_queue.get(timeout=15))
        except queue.Empty as exc:
            raise ProbeFailure("claim race worker did not report") from exc
    for proc in workers:
        proc.join(timeout=10)
        require(proc.exitcode == 0, f"claim worker exited {proc.exitcode}")

    errors = [item for item in race_results if item.get("error")]
    require(not errors, f"claim worker errors: {errors}")
    winners = [item for item in race_results if item.get("claim")]
    require(len(winners) == 1, f"expected exactly one claim winner, got {winners}")
    first_claim = Claim(**winners[0]["claim"])
    require(first_claim.fencing_token == 1, "first fencing token must be 1")

    before_heartbeat = fetch_job(dsn, first_claim.job_id)["lease_expires_at"]
    require(heartbeat(dsn, first_claim), "valid heartbeat was rejected")
    after_heartbeat = fetch_job(dsn, first_claim.job_id)["lease_expires_at"]
    require(after_heartbeat > before_heartbeat, "heartbeat did not extend the lease")
    require(event_count(dsn, first_claim.job_id, "HEARTBEAT") == 1, "heartbeat event missing")

    force_expire(dsn, first_claim.job_id)
    second_claim = claim_job(dsn, "worker-reassigned")
    require(second_claim is not None, "expired work was not reclaimable")
    require(second_claim.job_id == first_claim.job_id, "reassignment claimed wrong job")
    require(
        second_claim.fencing_token == first_claim.fencing_token + 1,
        "fencing token did not increment on reassignment",
    )

    stale_update_allowed = protected_update(dsn, first_claim, "STALE_SHOULD_NOT_WRITE")
    require(not stale_update_allowed, "stale worker mutated protected state")
    current_update_allowed = protected_update(dsn, second_claim, "CURRENT_WRITE_OK")
    require(current_update_allowed, "current lease holder could not mutate protected state")
    require(
        fetch_job(dsn, second_claim.job_id)["protected_value"] == "CURRENT_WRITE_OK",
        "protected state does not match current worker write",
    )

    transition_ok = transactional_transition(
        dsn,
        second_claim,
        expected_state="RUNNING",
        new_state="VERIFYING",
        event_type="STATE_TRANSITION",
    )
    require(transition_ok, "transactional state transition failed")
    require(fetch_job(dsn, second_claim.job_id)["state"] == "VERIFYING", "state transition missing")
    require(
        event_count(dsn, second_claim.job_id, "STATE_TRANSITION") == 1,
        "state transition event missing",
    )

    rollback_result = transactional_transition(
        dsn,
        second_claim,
        expected_state="VERIFYING",
        new_state="BROKEN",
        event_type="ROLLBACK_TEST",
        fail_before_commit=True,
    )
    require(not rollback_result, "intentional rollback unexpectedly committed")
    require(
        fetch_job(dsn, second_claim.job_id)["state"] == "VERIFYING",
        "state changed despite transaction rollback",
    )
    require(
        event_count(dsn, second_claim.job_id, "ROLLBACK_TEST") == 0,
        "event survived transaction rollback",
    )

    killed_claim = seed_kill_job(dsn)
    ready_queue = ctx.Queue()
    killed_proc = ctx.Process(target=uncommitted_transition_worker, args=(dsn, ready_queue))
    killed_proc.start()
    try:
        ready = ready_queue.get(timeout=15)
    except queue.Empty as exc:
        killed_proc.kill()
        killed_proc.join(timeout=5)
        raise ProbeFailure("kill-test worker never entered uncommitted transaction") from exc
    require(ready.get("ready") is True, f"kill-test worker failed before kill: {ready}")
    killed_proc.kill()
    killed_proc.join(timeout=10)
    require(killed_proc.exitcode not in (None, 0), "kill-test process was not actually killed")

    killed_after = fetch_job(dsn, "kill-job")
    require(killed_after["state"] == "RUNNING", "killed transaction leaked state mutation")
    require(
        event_count(dsn, "kill-job", "UNCOMMITTED_EVENT") == 0,
        "killed transaction leaked event append",
    )

    force_expire(dsn, "kill-job")
    recovered_claim = claim_job(dsn, "worker-reconciler")
    require(recovered_claim is not None, "killed worker job was not recoverable after lease expiry")
    require(recovered_claim.job_id == "kill-job", "recovery claimed wrong job")
    require(
        recovered_claim.fencing_token == killed_claim.fencing_token + 1,
        "recovery did not advance fencing token",
    )
    require(
        protected_update(dsn, recovered_claim, "RECOVERED_AFTER_KILL"),
        "reconciler could not mutate recovered job",
    )

    observation = database_observation(dsn)
    require(
        observation["advisory_lock_count"] == 0,
        "unexpected advisory lock dependence detected",
    )

    return {
        "status": "PASS",
        "claim_mechanism": "FOR UPDATE SKIP LOCKED",
        "claim_race": {
            "workers": len(workers),
            "winners": len(winners),
            "winner": asdict(first_claim),
        },
        "heartbeat_transactional": True,
        "lease_expiry_recoverable": True,
        "reassignment": {
            "old_token": first_claim.fencing_token,
            "new_token": second_claim.fencing_token,
        },
        "stale_worker_mutation_rejected": True,
        "current_worker_mutation_allowed": True,
        "state_and_event_atomic_commit": True,
        "state_and_event_atomic_rollback": True,
        "process_kill_rollback": True,
        "post_kill_recovery": {
            "old_token": killed_claim.fencing_token,
            "new_token": recovered_claim.fencing_token,
        },
        "advisory_locks_used": False,
        "database_observation": observation,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=os.environ.get("AMODEX_P0_04_DSN"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.dsn:
        raise SystemExit("AMODEX_P0_04_DSN or --dsn is required")

    result = run_probe(args.dsn)
    encoded = json.dumps(result, indent=2, sort_keys=True, default=str)
    print(encoded)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
