from __future__ import annotations

import os

from amodex.api.activation_status import activation_snapshot


def test_migrated_postgres_is_activation_ready(monkeypatch) -> None:
    for key in (
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "NVIDIA_API_KEY",
        "AMODEX_ACCESS_PASSCODE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AMODEX_STT_ENABLED", "false")

    dsn = os.environ["AMODEX_TEST_DATABASE_URL"]
    snapshot = activation_snapshot(
        dsn=dsn,
        app_version="test-activation",
        migrations_root="migrations",
    )

    assert snapshot["ready"] is True
    assert snapshot["status"] == "ready"
    assert snapshot["database"]["reachable"] is True
    assert snapshot["database"]["schema_compatible"] is True
    assert snapshot["database"]["missing_migrations"] == 0
    assert snapshot["database"]["checksum_mismatches"] == 0
    assert snapshot["zero_cost"]["ready"] is True
    assert snapshot["zero_cost"]["mode"] == "zero-cost-fail-closed"
