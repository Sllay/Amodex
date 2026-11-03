from __future__ import annotations

from datetime import datetime, timezone

from amodex.providers.capabilities import ModelMeasurement
from amodex.storage.model_intelligence import PostgresModelIntelligenceRepository
from amodex.storage.routing import PostgresRoutingRepository


class _OneRowResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _ExecuteConnection:
    def __init__(self, row=None) -> None:
        self.row = row
        self.calls: list[tuple[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return _OneRowResult(self.row)


class _Cursor:
    def __init__(self, *, previous=None, rows=()) -> None:
        self.previous = previous
        self.rows = rows
        self.calls: list[tuple[str, object]] = []
        self._last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self._last_sql = str(sql)
        self.calls.append((self._last_sql, params))
        return self

    def fetchone(self):
        if "FROM amodex_model_measurements" in self._last_sql:
            return self.previous
        return None

    def fetchall(self):
        return list(self.rows)


class _CursorConnection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self._cursor


def test_latest_benchmark_defaults_to_text_suite(monkeypatch) -> None:
    conn = _ExecuteConnection(
        {
            "benchmark_id": "text-run",
            "route_id": "route-1",
            "suite_version": "step4-live-v1",
        }
    )
    monkeypatch.setattr("amodex.storage.model_intelligence.psycopg.connect", lambda *a, **k: conn)
    repo = PostgresModelIntelligenceRepository("postgresql://test")

    row = repo.latest_benchmark("route-1")

    assert row is not None
    sql, params = conn.calls[-1]
    assert "suite_version=%s" in sql
    assert params == ("route-1", "step4-live-v1")


def test_text_measurement_preserves_existing_visual_score(monkeypatch) -> None:
    cursor = _Cursor(
        previous={
            "measurement_id": "vision-measurement",
            "sample_count": 5,
            "vision_success_rate": 0.92,
        }
    )
    monkeypatch.setattr(
        "amodex.storage.routing.psycopg.connect",
        lambda *a, **k: _CursorConnection(cursor),
    )
    repo = PostgresRoutingRepository("postgresql://test")
    repo.record_measurement(
        ModelMeasurement(
            provider="groq",
            model="qwen/qwen3.8-27b",
            sample_count=4,
            structured_tool_reliability=1.0,
            coding_pass_rate=0.8,
            correction_success_rate=0.8,
            planning_schema_compliance=1.0,
            research_success_rate=1.0,
            review_detection_rate=1.0,
            vision_success_rate=None,
            latency_ms_p50=100.0,
            observed_at=datetime.now(timezone.utc),
            source="step4-benchmark:text-run:step4-live-v1",
        )
    )

    insert = next((params for sql, params in cursor.calls if "INSERT INTO amodex_model_measurements" in sql), None)
    assert insert is not None
    assert insert[3] == 5
    assert insert[10] == 0.92
    assert str(insert[13]).endswith("|vision-preserved")


def test_shared_availability_ignores_legacy_visual_diagnostics(monkeypatch) -> None:
    cursor = _Cursor(rows=())
    monkeypatch.setattr(
        "amodex.storage.routing.psycopg.connect",
        lambda *a, **k: _CursorConnection(cursor),
    )
    repo = PostgresRoutingRepository("postgresql://test")

    assert repo.latest_availability() == ()
    sql = cursor.calls[-1][0]
    assert "reason NOT LIKE 'vision benchmark %'" in sql
