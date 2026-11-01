from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psycopg

from amodex.providers.governor import ProviderGovernor
from amodex.providers.simulator import DeterministicProvider
from amodex.runtime.worker import Stage1Worker
from amodex.storage.postgres import PostgresRunRepository
from amodex.storage.product import PostgresProductRepository
from amodex.storage.routing import PostgresRoutingRepository


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _prepare_database(dsn: str) -> None:
    runs = PostgresRunRepository(dsn)
    runs.migrate_all(Path("migrations"))
    with psycopg.connect(dsn) as conn:
        conn.execute(Path("migrations/stage4/0001_promotion.sql").read_text(encoding="utf-8"))
    PostgresRoutingRepository(dsn).migrate(Path("migrations/stage5/0001_routing_measurements.sql"))
    PostgresProductRepository(dsn).migrate(Path("migrations/stage6/0001_product_surfaces.sql"))
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """
            TRUNCATE amodex_audit_events, amodex_checkpoint_views, amodex_evidence_views,
                     amodex_task_views, amodex_runners, amodex_provider_connections,
                     amodex_approvals, amodex_run_controls, amodex_promotions,
                     amodex_candidates, amodex_provider_availability_observations,
                     amodex_model_measurements, amodex_run_events, amodex_runs,
                     amodex_projects CASCADE
            """
        )


def _cli(base_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "amodex.cli", "--url", base_url, *args],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_termux_cli_disconnect_and_reconnect_uses_remote_state() -> None:
    dsn = os.environ["AMODEX_TEST_DATABASE_URL"]
    _prepare_database(dsn)
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    env["AMODEX_DATABASE_URL"] = dsn
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "amodex.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                if httpx.get(base_url + "/ready", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError("Stage 6 uvicorn API did not become ready")

        project_cmd = _cli(base_url, "project", "add", "https://github.com/example/termux-demo")
        assert project_cmd.returncode == 0, project_cmd.stderr
        project = json.loads(project_cmd.stdout)

        run_cmd = _cli(
            base_url,
            "run",
            "prove terminal disconnect safety",
            "--project",
            project["project_id"],
        )
        assert run_cmd.returncode == 0, run_cmd.stderr
        run = json.loads(run_cmd.stdout)
        run_id = run["run_id"]

        # The process that created the run is gone. A fresh CLI process restores
        # status from the API/database rather than terminal history.
        status_before = _cli(base_url, "status", run_id)
        assert status_before.returncode == 0, status_before.stderr
        assert json.loads(status_before.stdout)["state"] == "QUEUED"

        worker = Stage1Worker(
            repository=PostgresRunRepository(dsn),
            provider=DeterministicProvider(),
            governor=ProviderGovernor.stage1_default(),
            worker_id="stage6-termux-worker",
        )
        completed = worker.process_once()
        assert completed is not None
        assert completed.run_id == run_id

        status_after = _cli(base_url, "status", run_id)
        assert status_after.returncode == 0, status_after.stderr
        observed = json.loads(status_after.stdout)
        assert observed["state"] == "COMPLETED"
        assert observed["result_text"] == "simulated:prove terminal disconnect safety"

        attach = _cli(base_url, "attach", run_id, "--once")
        assert attach.returncode == 0, attach.stderr
        assert "RUN_CREATED" in attach.stdout
        assert "RUN_COMPLETED" in attach.stdout

        system = _cli(base_url, "system")
        assert system.returncode == 0, system.stderr
        assert json.loads(system.stdout)["zero_cost_mode"] is True
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
