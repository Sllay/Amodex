from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from amodex.operations.backup import export_backup, restore_backup
from amodex.storage.postgres import PostgresRunRepository
from amodex.storage.product import PostgresProductRepository
from amodex.storage.routing import PostgresRoutingRepository


def _database_dsn(base_dsn: str, dbname: str) -> str:
    params = conninfo_to_dict(base_dsn)
    params["dbname"] = dbname
    return make_conninfo(**params)


def _create_database(base_dsn: str, dbname: str) -> str:
    admin = _database_dsn(base_dsn, "postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{dbname}"')
    return _database_dsn(base_dsn, dbname)


def _drop_database(base_dsn: str, dbname: str) -> None:
    admin = _database_dsn(base_dsn, "postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


def _migrate_through_stage5(dsn: str) -> None:
    runs = PostgresRunRepository(dsn)
    runs.migrate_all(Path("migrations"))
    with psycopg.connect(dsn) as conn:
        conn.execute(Path("migrations/stage4/0001_promotion.sql").read_text(encoding="utf-8"))
    PostgresRoutingRepository(dsn).migrate(Path("migrations/stage5/0001_routing_measurements.sql"))


def _migrate_stage6(dsn: str) -> PostgresProductRepository:
    product = PostgresProductRepository(dsn)
    product.migrate(Path("migrations/stage6/0001_product_surfaces.sql"))
    return product


def test_stage6_upgrade_preserves_preexisting_runtime_state_on_copy() -> None:
    base = os.environ["AMODEX_TEST_DATABASE_URL"]
    dbname = "amodex_stage6_upgrade_copy"
    dsn = _create_database(base, dbname)
    try:
        _migrate_through_stage5(dsn)
        runs = PostgresRunRepository(dsn)
        original = runs.create_run(prompt="pre-stage6 durable state")
        assert original.project_id is None

        product = _migrate_stage6(dsn)
        assert product.schema_ready()
        restored = runs.get_run(original.run_id)
        assert restored is not None
        assert restored.prompt == "pre-stage6 durable state"
        assert restored.project_id is None

        project = product.create_project(repo_url="https://github.com/example/upgrade", name="upgrade")
        after = runs.create_run(prompt="post-upgrade state", project_id=project.project_id)
        assert after.project_id == project.project_id
    finally:
        _drop_database(base, dbname)


def test_logical_backup_restores_authoritative_state_into_separate_database() -> None:
    base = os.environ["AMODEX_TEST_DATABASE_URL"]
    source_name = "amodex_stage6_backup_source"
    target_name = "amodex_stage6_backup_target"
    source = _create_database(base, source_name)
    target = _create_database(base, target_name)
    try:
        _migrate_through_stage5(source)
        source_product = _migrate_stage6(source)
        source_runs = PostgresRunRepository(source)
        project = source_product.create_project(
            repo_url="https://github.com/example/backup",
            name="backup-project",
        )
        run = source_runs.create_run(prompt="durable backup objective", project_id=project.project_id)
        source_product.set_run_control(
            run_id=run.run_id,
            desired_state=__import__("amodex.product.models", fromlist=["RunDesiredState"]).RunDesiredState.PAUSED,
            reason="snapshot while paused",
        )
        source_product.create_approval(
            action="PROMOTE",
            subject_type="candidate",
            subject_id="candidate-backup",
            impact="promotion requires owner confirmation",
            expires_at=None,
        )

        bundle = export_backup(source)
        assert bundle.sha256
        assert bundle.counts["amodex_projects"] == 1
        assert bundle.counts["amodex_runs"] == 1
        assert bundle.counts["amodex_run_events"] >= 2
        assert all("api_key" not in row for rows in bundle.tables.values() for row in rows)

        _migrate_through_stage5(target)
        target_product = _migrate_stage6(target)
        restored_counts = restore_backup(target, bundle, allow_destructive_restore=True)
        assert restored_counts == bundle.counts
        assert target_product.list_projects()[0].project_id == project.project_id
        restored_run = PostgresRunRepository(target).get_run(run.run_id)
        assert restored_run is not None
        assert restored_run.prompt == "durable backup objective"
        assert target_product.get_run_control(run.run_id) is not None
    finally:
        _drop_database(base, source_name)
        _drop_database(base, target_name)
