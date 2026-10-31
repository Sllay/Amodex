from __future__ import annotations

import os
from pathlib import Path

import psycopg

from amodex.storage.postgres import PostgresRunRepository


def test_migrations_apply_from_clean_database_and_are_idempotent() -> None:
    dsn = os.environ["AMODEX_TEST_DATABASE_URL"]
    with psycopg.connect(dsn) as conn:
        conn.execute("DROP TABLE IF EXISTS amodex_run_events CASCADE")
        conn.execute("DROP TABLE IF EXISTS amodex_runs CASCADE")
        conn.execute("DROP TABLE IF EXISTS amodex_schema_migrations CASCADE")

    repo = PostgresRunRepository(dsn)
    first = repo.migrate_all(Path("migrations"))
    second = repo.migrate_all(Path("migrations"))

    assert first == (
        "0001_stage1_runtime.sql",
        "0002_provider_request_intent.sql",
    )
    assert second == ()

    with psycopg.connect(dsn) as conn:
        versions = tuple(
            row[0]
            for row in conn.execute(
                "SELECT version FROM amodex_schema_migrations ORDER BY version"
            ).fetchall()
        )
        columns = {
            row[0]
            for row in conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='amodex_runs'
                """
            ).fetchall()
        }

    assert versions == first
    assert {"provider_request_key", "provider_request_state"} <= columns
