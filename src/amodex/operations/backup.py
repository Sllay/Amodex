"""Versioned logical PostgreSQL backup/restore for Stage 6 operations.

This is a trusted maintenance path, not a worker tool. It intentionally exports
only Amodex authority/projection tables and never environment secrets.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import psycopg


BACKUP_SCHEMA_VERSION = 1
BACKUP_TABLES: tuple[str, ...] = (
    "amodex_projects",
    "amodex_runs",
    "amodex_run_events",
    "amodex_run_controls",
    "amodex_candidates",
    "amodex_promotions",
    "amodex_model_measurements",
    "amodex_provider_availability_observations",
    "amodex_approvals",
    "amodex_provider_connections",
    "amodex_runners",
    "amodex_task_views",
    "amodex_evidence_views",
    "amodex_checkpoint_views",
    "amodex_audit_events",
    "amodex_product_settings",
)


@dataclass(frozen=True, slots=True)
class BackupBundle:
    schema_version: int
    created_at: str
    tables: dict[str, list[dict[str, Any]]]
    sha256: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "created_at": self.created_at,
                "tables": self.tables,
                "sha256": self.sha256,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> "BackupBundle":
        data = json.loads(raw)
        return cls(
            schema_version=int(data["schema_version"]),
            created_at=str(data["created_at"]),
            tables={str(k): list(v) for k, v in data["tables"].items()},
            sha256=str(data["sha256"]),
        )

    @property
    def counts(self) -> dict[str, int]:
        return {table: len(rows) for table, rows in self.tables.items()}


def export_backup(dsn: str) -> BackupBundle:
    tables: dict[str, list[dict[str, Any]]] = {}
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for table in BACKUP_TABLES:
                if not _table_exists(cur, table):
                    tables[table] = []
                    continue
                cur.execute(f"SELECT row_to_json(t)::text FROM (SELECT * FROM {table}) AS t")
                tables[table] = [json.loads(row[0]) for row in cur.fetchall()]
    created_at = datetime.now(timezone.utc).isoformat()
    digest = _digest_payload(BACKUP_SCHEMA_VERSION, tables)
    return BackupBundle(
        schema_version=BACKUP_SCHEMA_VERSION,
        created_at=created_at,
        tables=tables,
        sha256=digest,
    )


def restore_backup(
    dsn: str,
    bundle: BackupBundle,
    *,
    allow_destructive_restore: bool = False,
) -> dict[str, int]:
    if bundle.schema_version != BACKUP_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported backup schema {bundle.schema_version}; expected {BACKUP_SCHEMA_VERSION}"
        )
    expected = _digest_payload(bundle.schema_version, bundle.tables)
    if expected != bundle.sha256:
        raise ValueError("backup digest mismatch")
    unknown = set(bundle.tables) - set(BACKUP_TABLES)
    if unknown:
        raise ValueError(f"backup contains unknown tables: {sorted(unknown)!r}")

    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                existing_nonempty: list[str] = []
                for table in BACKUP_TABLES:
                    if not _table_exists(cur, table):
                        raise ValueError(f"target schema missing table {table}")
                    cur.execute(f"SELECT EXISTS(SELECT 1 FROM {table} LIMIT 1)")
                    if bool(cur.fetchone()[0]):
                        existing_nonempty.append(table)
                if existing_nonempty and not allow_destructive_restore:
                    raise ValueError(
                        "restore target is not empty; explicit allow_destructive_restore is required: "
                        + ", ".join(existing_nonempty)
                    )
                if existing_nonempty:
                    cur.execute(
                        "TRUNCATE " + ",".join(BACKUP_TABLES) + " RESTART IDENTITY CASCADE"
                    )

                for table in BACKUP_TABLES:
                    rows = bundle.tables.get(table, [])
                    if not rows:
                        continue
                    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True)
                    cur.execute(
                        f"INSERT INTO {table} SELECT * FROM json_populate_recordset(NULL::{table}, %s::json)",
                        (payload,),
                    )
    restored = export_backup(dsn)
    if restored.counts != bundle.counts:
        raise ValueError(
            f"restore verification count mismatch: expected {bundle.counts}, got {restored.counts}"
        )
    return restored.counts


def _digest_payload(schema_version: int, tables: dict[str, list[dict[str, Any]]]) -> str:
    material = json.dumps(
        {"schema_version": schema_version, "tables": tables},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _table_exists(cur: psycopg.Cursor[Any], table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
    return bool(cur.fetchone()[0])
