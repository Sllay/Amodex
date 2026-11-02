from __future__ import annotations

import hashlib

import pytest

from amodex.deployment.migrations import (
    database_endpoint_identity,
    discover_production_migrations,
    validate_database_target,
)


RUNTIME_NEON = (
    "postgresql://amodex:secret@"
    "ep-amodex-safe-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require"
)
MIGRATION_NEON = (
    "postgresql://amodex:secret@"
    "ep-amodex-safe.us-east-2.aws.neon.tech/neondb?sslmode=require"
)


def test_neon_runtime_and_migration_urls_must_match_guard() -> None:
    assert database_endpoint_identity(RUNTIME_NEON) == "ep-amodex-safe"
    assert (
        validate_database_target(
            runtime_dsn=RUNTIME_NEON,
            migration_dsn=MIGRATION_NEON,
            endpoint_guard="ep-amodex-safe",
        )
        == "ep-amodex-safe"
    )


def test_database_guard_blocks_accidental_other_neon_project() -> None:
    old_project = MIGRATION_NEON.replace("ep-amodex-safe", "ep-old-project")
    with pytest.raises(ValueError, match="different endpoints"):
        validate_database_target(
            runtime_dsn=RUNTIME_NEON,
            migration_dsn=old_project,
            endpoint_guard="ep-amodex-safe",
        )

    with pytest.raises(ValueError, match="guard does not match"):
        validate_database_target(
            runtime_dsn=RUNTIME_NEON,
            migration_dsn=MIGRATION_NEON,
            endpoint_guard="ep-old-project",
        )


def test_neon_runtime_requires_pooler_and_migrations_require_direct_endpoint() -> None:
    with pytest.raises(ValueError, match="runtime.*pooled endpoint"):
        validate_database_target(
            runtime_dsn=MIGRATION_NEON,
            migration_dsn=MIGRATION_NEON,
            endpoint_guard="ep-amodex-safe",
        )

    pooled_migration = RUNTIME_NEON
    with pytest.raises(ValueError, match="migration.*direct Neon endpoint"):
        validate_database_target(
            runtime_dsn=RUNTIME_NEON,
            migration_dsn=pooled_migration,
            endpoint_guard="ep-amodex-safe",
        )


def test_neon_urls_must_require_tls() -> None:
    without_ssl = RUNTIME_NEON.split("?", 1)[0]
    with pytest.raises(ValueError, match="require TLS"):
        database_endpoint_identity(without_ssl)


def test_discovery_is_deterministic_and_checksums_sql(tmp_path) -> None:
    groups = ("stage4", "stage5", "stage6", "postbaseline")
    for group in groups:
        (tmp_path / group).mkdir()

    files = {
        "0001_root.sql": "SELECT 1;\n",
        "stage4/0001_stage4.sql": "SELECT 4;\n",
        "stage5/0001_stage5.sql": "SELECT 5;\n",
        "stage6/0001_stage6.sql": "SELECT 6;\n",
        "postbaseline/0001_post.sql": "SELECT 7;\n",
    }
    for relative, sql in files.items():
        path = tmp_path / relative
        path.write_text(sql, encoding="utf-8")

    discovered = discover_production_migrations(tmp_path)
    assert [migration.version for migration in discovered] == list(files)
    assert discovered[0].checksum_sha256 == hashlib.sha256(
        files["0001_root.sql"].encode("utf-8")
    ).hexdigest()
