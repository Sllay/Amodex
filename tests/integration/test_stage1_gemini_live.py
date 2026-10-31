from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

from amodex.providers.gemini import GeminiProvider
from amodex.providers.governor import ProviderGovernor
from amodex.runtime.models import RunState
from amodex.runtime.worker import Stage1Worker
from amodex.storage.postgres import PostgresRunRepository


def test_real_free_gemini_path_is_durable() -> None:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        pytest.skip("GEMINI_API_KEY is not configured")

    dsn = os.environ["AMODEX_TEST_DATABASE_URL"]
    repo = PostgresRunRepository(dsn)
    repo.migrate_all(Path("migrations"))
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE amodex_run_events, amodex_runs CASCADE")

    created = repo.create_run(
        prompt="Reply with one short sentence confirming AMODEX_STAGE1_OK."
    )
    worker = Stage1Worker(
        repository=repo,
        provider=GeminiProvider(api_key=api_key),
        governor=ProviderGovernor.stage1_default(),
        worker_id="stage1-live-gemini",
    )
    completed = worker.process_once()

    assert completed is not None
    assert completed.run_id == created.run_id
    assert completed.state == RunState.COMPLETED
    assert completed.provider == "gemini"
    assert completed.model == "gemini-3.5-flash-lite"
    assert completed.result_text

    reread = repo.get_run(created.run_id)
    assert reread is not None
    assert reread.state == RunState.COMPLETED
    assert reread.result_text == completed.result_text

    serialized_events = "\n".join(
        event.model_dump_json() for event in repo.list_events(created.run_id)
    )
    assert api_key not in serialized_events
