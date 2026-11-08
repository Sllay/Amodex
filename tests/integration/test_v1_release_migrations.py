"""Release A: actual migration upgrade/rollback on disposable loopback databases."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
from urllib.parse import urlparse
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

from amodex.api.activation_status import database_readiness
from amodex.api.app import create_app
from amodex.contracts.ids import new_uuid7
from amodex.deployment.migrations import apply_production_migrations, database_endpoint_identity
from amodex.storage.environment_product import PostgresEnvironmentProductRepository
from amodex.storage.postgres import PostgresRunRepository
from test_postbaseline_environment_product import _create_scoped_run

LATE = ("postbaseline/0008_runner_dispatch_idempotency.sql", "postbaseline/0009_runner_terminal_receipts.sql")


@pytest.fixture
def prior_schema(tmp_path):
    source = os.environ["AMODEX_TEST_DATABASE_URL"]
    target = urlparse(source)
    assert target.hostname in {"127.0.0.1", "localhost", "::1"} and target.path.startswith("/amodex_qa_")
    database = "amodex_qa_v1_" + uuid4().hex
    admin = make_conninfo(source, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    dsn = make_conninfo(source, dbname=database)
    old = tmp_path / "prior-migrations"
    shutil.copytree("migrations", old)
    for name in LATE: (old / name).unlink()
    # make_conninfo uses keyword form; migration target guards intentionally use URLs.
    parsed = target._replace(path="/"+database)
    uri = parsed.geturl()
    apply = lambda root: apply_production_migrations(runtime_dsn=uri,migration_dsn=uri,
        endpoint_guard=database_endpoint_identity(uri),migrations_root=root)
    try:
        apply(old)
        with TestClient(create_app(PostgresRunRepository(uri))) as client:
            project,env,session,run = _create_scoped_run(client,name="release legacy",engine="generic",
                repo_url="https://github.com/qa-local-fixture/release-upgrade")
        job = new_uuid7()
        with psycopg.connect(dsn) as conn:
            conn.execute("""INSERT INTO amodex_environment_runner_jobs
                (job_id,environment_id,session_id,run_id,project_id,engine,runner_kind,
                 worker_profile,task_class,write_paths,state,summary,completed_at)
                VALUES (%s,%s,%s,%s,%s,'generic','ACTIONS_HEADLESS','implementation','STANDARD',
                        '[]'::jsonb,'COMPLETED','legacy, unconfirmed',NOW())""",
                (job,env['environment_id'],session['session_id'],run['run_id'],project['project_id']))
        yield dsn,uri,apply,job
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))


def test_upgrade_and_reentry_preserve_legacy_without_fabricating_receipt(prior_schema):
    dsn,uri,apply,job = prior_schema
    before = database_readiness(uri,migrations_root="migrations")
    assert not before['schema_compatible'] and before['missing_migrations']==2
    upgrade = apply("migrations")
    assert set(upgrade.applied)==set(LATE)
    assert apply("migrations").applied==()
    assert database_readiness(uri,migrations_root="migrations")['schema_compatible']
    repo = PostgresEnvironmentProductRepository(dsn)
    legacy = repo.get_runner_job(job)
    assert legacy.state.value=='COMPLETED' and legacy.summary=='legacy, unconfirmed'
    assert legacy.execution_attempt_id is None and legacy.terminal_receipt_id is None
    assert repo.begin_dispatch(job_id=job,target={}) is None
    with psycopg.connect(dsn) as conn:
        assert conn.execute('SELECT count(*) FROM amodex_runner_terminal_receipts').fetchone()[0]==0


def test_transactional_migration_rollback_preserves_prior_schema_and_data(prior_schema):
    dsn,uri,_,job = prior_schema
    class RollbackDrill(Exception): pass
    with pytest.raises(RollbackDrill):
        with psycopg.connect(dsn) as conn:
            for name in LATE: conn.execute((Path('migrations')/name).read_text())
            raise RollbackDrill()
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT to_regclass('amodex_runner_terminal_receipts')").fetchone()[0] is None
        assert conn.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='amodex_environment_runner_jobs' AND column_name IN ('dispatch_status','terminal_receipt_id')").fetchone()[0]==0
        assert conn.execute('SELECT state,summary FROM amodex_environment_runner_jobs WHERE job_id=%s',(job,)).fetchone()==('COMPLETED','legacy, unconfirmed')
    assert not database_readiness(uri,migrations_root="migrations")['schema_compatible']
