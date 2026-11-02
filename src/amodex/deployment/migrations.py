"""Guarded, deterministic production migration execution.

The production database is intentionally split into two connection strings:

* ``AMODEX_DATABASE_URL``: pooled runtime connection for the web service.
* ``AMODEX_MIGRATION_DATABASE_URL``: direct connection used only for migrations.

For Neon targets, both URLs must identify the same endpoint, the runtime URL must
use the pooler, the migration URL must be direct, and an explicit endpoint guard
must match. This makes accidentally pasting a connection string from another
Neon project fail before any schema write occurs.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psycopg

_MIGRATION_GROUPS = ("", "stage4", "stage5", "stage6", "postbaseline")
_MIGRATION_LOCK_KEY = 71_684_610_887_000  # stable session-level advisory lock
_ALLOWED_SSL_MODES = {"require", "verify-ca", "verify-full"}


@dataclass(frozen=True, slots=True)
class MigrationFile:
    version: str
    path: Path
    checksum_sha256: str


@dataclass(frozen=True, slots=True)
class MigrationResult:
    endpoint_identity: str
    applied: tuple[str, ...]
    skipped: tuple[str, ...]
    checksum_backfilled: tuple[str, ...]


def discover_production_migrations(root: str | Path) -> tuple[MigrationFile, ...]:
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise ValueError(f"migration directory does not exist: {root_path}")

    discovered: list[MigrationFile] = []
    seen_versions: set[str] = set()
    for group in _MIGRATION_GROUPS:
        directory = root_path / group if group else root_path
        if not directory.is_dir():
            raise ValueError(f"required migration group is missing: {directory}")
        for path in sorted(directory.glob("*.sql")):
            relative = path.relative_to(root_path).as_posix()
            if relative in seen_versions:
                raise ValueError(f"duplicate migration version: {relative}")
            seen_versions.add(relative)
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            discovered.append(MigrationFile(relative, path, checksum))

    if not discovered:
        raise ValueError(f"no SQL migrations found in {root_path}")
    return tuple(discovered)


def _parse_database_url(dsn: str, *, label: str) -> tuple[str, bool]:
    if not dsn:
        raise ValueError(f"{label} is required")
    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError(f"{label} must be a PostgreSQL URL")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError(f"{label} must include a database host")

    is_neon = host.endswith(".neon.tech")
    if is_neon:
        query = parse_qs(parsed.query)
        ssl_mode = (query.get("sslmode") or [""])[-1].strip().lower()
        if ssl_mode not in _ALLOWED_SSL_MODES:
            raise ValueError(f"{label} must require TLS for Neon")
    return host, is_neon


def _endpoint_identity_from_host(host: str) -> str:
    first_label = host.split(".", 1)[0]
    if first_label.endswith("-pooler"):
        first_label = first_label.removesuffix("-pooler")
    return first_label


def database_endpoint_identity(dsn: str) -> str:
    host, _ = _parse_database_url(dsn, label="database URL")
    return _endpoint_identity_from_host(host)


def validate_database_target(
    *,
    runtime_dsn: str,
    migration_dsn: str,
    endpoint_guard: str,
) -> str:
    runtime_host, runtime_is_neon = _parse_database_url(runtime_dsn, label="AMODEX_DATABASE_URL")
    migration_host, migration_is_neon = _parse_database_url(
        migration_dsn,
        label="AMODEX_MIGRATION_DATABASE_URL",
    )
    runtime_identity = _endpoint_identity_from_host(runtime_host)
    migration_identity = _endpoint_identity_from_host(migration_host)
    normalized_guard = endpoint_guard.strip().lower().removesuffix("-pooler")

    if not normalized_guard:
        raise ValueError("AMODEX_DATABASE_ENDPOINT_GUARD is required")
    if runtime_identity != migration_identity:
        raise ValueError("runtime and migration database URLs target different endpoints")
    if runtime_identity != normalized_guard:
        raise ValueError("database endpoint guard does not match the configured database")
    if runtime_is_neon != migration_is_neon:
        raise ValueError("runtime and migration database URLs use incompatible hosts")
    if runtime_is_neon:
        if not runtime_host.split(".", 1)[0].endswith("-pooler"):
            raise ValueError("AMODEX_DATABASE_URL must use the Neon pooled endpoint")
        if migration_host.split(".", 1)[0].endswith("-pooler"):
            raise ValueError("AMODEX_MIGRATION_DATABASE_URL must use the direct Neon endpoint")
    return runtime_identity


def _ensure_migration_table(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS amodex_schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    checksum_sha256 TEXT NULL
                )
                """
            )
            cur.execute(
                """
                ALTER TABLE amodex_schema_migrations
                ADD COLUMN IF NOT EXISTS checksum_sha256 TEXT NULL
                """
            )


def apply_production_migrations(
    *,
    runtime_dsn: str,
    migration_dsn: str,
    endpoint_guard: str,
    migrations_root: str | Path,
) -> MigrationResult:
    endpoint_identity = validate_database_target(
        runtime_dsn=runtime_dsn,
        migration_dsn=migration_dsn,
        endpoint_guard=endpoint_guard,
    )
    migrations = discover_production_migrations(migrations_root)
    applied: list[str] = []
    skipped: list[str] = []
    backfilled: list[str] = []

    with psycopg.connect(migration_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK_KEY,))
        try:
            _ensure_migration_table(conn)
            for migration in migrations:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT checksum_sha256 FROM amodex_schema_migrations WHERE version=%s",
                        (migration.version,),
                    )
                    row = cur.fetchone()

                if row is not None:
                    recorded_checksum = row[0]
                    if recorded_checksum is None:
                        with conn.transaction():
                            with conn.cursor() as cur:
                                cur.execute(
                                    """
                                    UPDATE amodex_schema_migrations
                                    SET checksum_sha256=%s
                                    WHERE version=%s AND checksum_sha256 IS NULL
                                    """,
                                    (migration.checksum_sha256, migration.version),
                                )
                        backfilled.append(migration.version)
                        skipped.append(migration.version)
                        continue
                    if str(recorded_checksum) != migration.checksum_sha256:
                        raise RuntimeError(
                            f"applied migration checksum changed: {migration.version}"
                        )
                    skipped.append(migration.version)
                    continue

                sql = migration.path.read_text(encoding="utf-8")
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(sql)
                        cur.execute(
                            """
                            INSERT INTO amodex_schema_migrations (version, checksum_sha256)
                            VALUES (%s, %s)
                            """,
                            (migration.version, migration.checksum_sha256),
                        )
                applied.append(migration.version)
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK_KEY,))

    return MigrationResult(
        endpoint_identity=endpoint_identity,
        applied=tuple(applied),
        skipped=tuple(skipped),
        checksum_backfilled=tuple(backfilled),
    )


def main() -> int:
    runtime_dsn = os.environ.get("AMODEX_DATABASE_URL", "").strip()
    migration_dsn = os.environ.get("AMODEX_MIGRATION_DATABASE_URL", "").strip()
    endpoint_guard = os.environ.get("AMODEX_DATABASE_ENDPOINT_GUARD", "").strip()
    migrations_root = os.environ.get("AMODEX_MIGRATIONS_DIR", "migrations").strip() or "migrations"

    result = apply_production_migrations(
        runtime_dsn=runtime_dsn,
        migration_dsn=migration_dsn,
        endpoint_guard=endpoint_guard,
        migrations_root=migrations_root,
    )
    print(f"DATABASE_TARGET_OK endpoint={result.endpoint_identity}")
    for version in result.applied:
        print(f"MIGRATION_APPLIED {version}")
    for version in result.checksum_backfilled:
        print(f"MIGRATION_CHECKSUM_BACKFILLED {version}")
    print(
        "MIGRATIONS_OK "
        f"applied={len(result.applied)} skipped={len(result.skipped)}"
    )
    return 0
