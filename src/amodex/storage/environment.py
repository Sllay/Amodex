"""PostgreSQL persistence for isolated post-baseline Environments.

This repository deliberately requires environment identifiers on every scoped read
or write. Operator-wide listing is limited to environments themselves; worker-facing
memory/run/project access is always environment-qualified.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from amodex.contracts.ids import new_uuid7
from amodex.environments.models import (
    EnvironmentRecord,
    EnvironmentStatus,
    MemoryRecord,
    MemoryScope,
    SessionRecord,
    SessionStatus,
)


class EnvironmentStateError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class PostgresEnvironmentRepository:
    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    @property
    def dsn(self) -> str:
        return self._dsn

    def migrate(self, migration_path: str | Path) -> None:
        sql = Path(migration_path).read_text(encoding="utf-8")
        with psycopg.connect(self._dsn) as conn:
            conn.execute(sql)

    def schema_ready(self) -> bool:
        required = {
            "amodex_environments",
            "amodex_environment_projects",
            "amodex_sessions",
            "amodex_session_runs",
            "amodex_memories",
            "amodex_environment_usage_events",
            "amodex_environment_budget_policies",
        }
        with psycopg.connect(self._dsn) as conn:
            rows = conn.execute(
                "SELECT relname FROM pg_class WHERE relname = ANY(%s)",
                (list(required),),
            ).fetchall()
        return {row[0] for row in rows} == required

    def create_environment(
        self,
        *,
        name: str,
        engine: str | None = None,
    ) -> EnvironmentRecord:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("environment name must be non-empty")
        environment_id = new_uuid7()
        now = datetime.now(timezone.utc)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                INSERT INTO amodex_environments(
                    environment_id,name,engine,status,created_at,updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s)
                RETURNING *
                """,
                (
                    environment_id,
                    clean_name,
                    engine.strip() if engine and engine.strip() else None,
                    EnvironmentStatus.ACTIVE.value,
                    now,
                    now,
                ),
            ).fetchone()
        assert row is not None
        return _environment_from_row(row)

    def get_environment(self, environment_id: str) -> EnvironmentRecord | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT * FROM amodex_environments WHERE environment_id=%s",
                (environment_id,),
            ).fetchone()
        return _environment_from_row(row) if row else None

    def list_environments(self) -> tuple[EnvironmentRecord, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT * FROM amodex_environments ORDER BY created_at, environment_id"
            ).fetchall()
        return tuple(_environment_from_row(row) for row in rows)

    def bind_project(self, *, environment_id: str, project_id: str) -> None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                env = conn.execute(
                    "SELECT status FROM amodex_environments WHERE environment_id=%s FOR SHARE",
                    (environment_id,),
                ).fetchone()
                if env is None:
                    raise KeyError(environment_id)
                if env["status"] != EnvironmentStatus.ACTIVE.value:
                    raise EnvironmentStateError("ENVIRONMENT_DISABLED", environment_id)

                project = conn.execute(
                    "SELECT project_id FROM amodex_projects WHERE project_id=%s FOR SHARE",
                    (project_id,),
                ).fetchone()
                if project is None:
                    raise KeyError(project_id)

                existing = conn.execute(
                    "SELECT environment_id FROM amodex_environment_projects WHERE project_id=%s",
                    (project_id,),
                ).fetchone()
                if existing is not None:
                    if str(existing["environment_id"]) == environment_id:
                        return
                    raise EnvironmentStateError(
                        "PROJECT_ALREADY_BOUND",
                        f"project={project_id} environment={existing['environment_id']}",
                    )

                conn.execute(
                    """
                    INSERT INTO amodex_environment_projects(environment_id,project_id,bound_at)
                    VALUES (%s,%s,NOW())
                    """,
                    (environment_id, project_id),
                )

    def project_environment(self, project_id: str) -> str | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT environment_id FROM amodex_environment_projects WHERE project_id=%s",
                (project_id,),
            ).fetchone()
        return str(row["environment_id"]) if row else None

    def create_session(
        self,
        *,
        environment_id: str,
        title: str,
        domain_key: str | None = None,
    ) -> SessionRecord:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("session title must be non-empty")
        environment = self.get_environment(environment_id)
        if environment is None:
            raise KeyError(environment_id)
        if environment.status != EnvironmentStatus.ACTIVE:
            raise EnvironmentStateError(
                "ENVIRONMENT_DISABLED",
                f"cannot create session in {environment_id}",
            )
        session_id = new_uuid7()
        now = datetime.now(timezone.utc)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                INSERT INTO amodex_sessions(
                    session_id,environment_id,title,domain_key,status,created_at,updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
                """,
                (
                    session_id,
                    environment_id,
                    clean_title,
                    domain_key.strip() if domain_key and domain_key.strip() else None,
                    SessionStatus.ACTIVE.value,
                    now,
                    now,
                ),
            ).fetchone()
        assert row is not None
        return _session_from_row(row)

    def list_sessions(self, *, environment_id: str) -> tuple[SessionRecord, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                """
                SELECT * FROM amodex_sessions
                WHERE environment_id=%s
                ORDER BY created_at, session_id
                """,
                (environment_id,),
            ).fetchall()
        return tuple(_session_from_row(row) for row in rows)

    def bind_run(
        self,
        *,
        environment_id: str,
        session_id: str,
        run_id: str,
    ) -> None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                session = conn.execute(
                    """
                    SELECT environment_id FROM amodex_sessions
                    WHERE session_id=%s FOR SHARE
                    """,
                    (session_id,),
                ).fetchone()
                if session is None:
                    raise KeyError(session_id)
                if str(session["environment_id"]) != environment_id:
                    raise EnvironmentStateError(
                        "SESSION_ENVIRONMENT_MISMATCH",
                        f"session={session_id} environment={session['environment_id']}",
                    )

                run = conn.execute(
                    "SELECT project_id FROM amodex_runs WHERE run_id=%s FOR SHARE",
                    (run_id,),
                ).fetchone()
                if run is None:
                    raise KeyError(run_id)
                if run["project_id"] is None:
                    raise EnvironmentStateError(
                        "UNSCOPED_RUN_DENIED",
                        "environment sessions require project-bound runs",
                    )

                project_env = conn.execute(
                    """
                    SELECT environment_id FROM amodex_environment_projects
                    WHERE project_id=%s
                    """,
                    (run["project_id"],),
                ).fetchone()
                if project_env is None:
                    raise EnvironmentStateError(
                        "UNBOUND_PROJECT_DENIED",
                        str(run["project_id"]),
                    )
                if str(project_env["environment_id"]) != environment_id:
                    raise EnvironmentStateError(
                        "RUN_ENVIRONMENT_MISMATCH",
                        f"run={run_id} project_environment={project_env['environment_id']}",
                    )

                existing = conn.execute(
                    "SELECT environment_id,session_id FROM amodex_session_runs WHERE run_id=%s",
                    (run_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        str(existing["environment_id"]) == environment_id
                        and str(existing["session_id"]) == session_id
                    ):
                        return
                    raise EnvironmentStateError(
                        "RUN_ALREADY_BOUND",
                        f"run={run_id} environment={existing['environment_id']} session={existing['session_id']}",
                    )

                conn.execute(
                    """
                    INSERT INTO amodex_session_runs(run_id,session_id,environment_id,bound_at)
                    VALUES (%s,%s,%s,NOW())
                    """,
                    (run_id, session_id, environment_id),
                )

    def run_environment(self, run_id: str) -> str | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT environment_id FROM amodex_session_runs WHERE run_id=%s",
                (run_id,),
            ).fetchone()
        return str(row["environment_id"]) if row else None

    def put_memory(
        self,
        *,
        scope: MemoryScope,
        content: str,
        source_ref: str,
        environment_id: str | None = None,
        session_id: str | None = None,
        domain_key: str | None = None,
    ) -> MemoryRecord:
        clean = content.strip()
        source = source_ref.strip()
        if not clean or not source:
            raise ValueError("memory content/source_ref must be non-empty")
        _validate_memory_binding(
            scope=scope,
            environment_id=environment_id,
            session_id=session_id,
            domain_key=domain_key,
        )
        if session_id is not None:
            with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
                session = conn.execute(
                    "SELECT environment_id FROM amodex_sessions WHERE session_id=%s",
                    (session_id,),
                ).fetchone()
            if session is None:
                raise KeyError(session_id)
            if str(session["environment_id"]) != environment_id:
                raise EnvironmentStateError("SESSION_ENVIRONMENT_MISMATCH", session_id)

        digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            existing = conn.execute(
                """
                SELECT * FROM amodex_memories
                WHERE scope=%s
                  AND environment_id IS NOT DISTINCT FROM %s
                  AND session_id IS NOT DISTINCT FROM %s
                  AND domain_key IS NOT DISTINCT FROM %s
                  AND content_sha256=%s
                """,
                (scope.value, environment_id, session_id, domain_key, digest),
            ).fetchone()
            if existing is not None:
                return _memory_from_row(existing)

            row = conn.execute(
                """
                INSERT INTO amodex_memories(
                    memory_id,scope,environment_id,session_id,domain_key,
                    content,source_ref,content_sha256,created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                RETURNING *
                """,
                (
                    new_uuid7(),
                    scope.value,
                    environment_id,
                    session_id,
                    domain_key,
                    clean,
                    source,
                    digest,
                ),
            ).fetchone()
        assert row is not None
        return _memory_from_row(row)

    def list_visible_memories(
        self,
        *,
        environment_id: str,
        session_id: str | None = None,
        domain_key: str | None = None,
        include_owner: bool = True,
        limit: int = 200,
    ) -> tuple[MemoryRecord, ...]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        clauses: list[str] = []
        params: list[Any] = []
        if include_owner:
            clauses.append("scope='OWNER'")
        clauses.append("(scope='ENVIRONMENT' AND environment_id=%s)")
        params.append(environment_id)
        if domain_key is not None:
            clauses.append("(scope='DOMAIN' AND environment_id=%s AND domain_key=%s)")
            params.extend((environment_id, domain_key))
        if session_id is not None:
            clauses.append("(scope='SESSION' AND environment_id=%s AND session_id=%s)")
            params.extend((environment_id, session_id))

        where = " OR ".join(clauses)
        params.append(limit)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM amodex_memories
                WHERE {where}
                ORDER BY created_at, memory_id
                LIMIT %s
                """,
                tuple(params),
            ).fetchall()
        return tuple(_memory_from_row(row) for row in rows)

    def record_usage(
        self,
        *,
        environment_id: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        requests: int,
        outcome: str,
        run_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
    ) -> str:
        if min(input_tokens, output_tokens, requests) < 0:
            raise ValueError("usage values cannot be negative")
        if requests == 0 and (input_tokens or output_tokens):
            raise ValueError("token usage requires at least one request")
        if run_id is not None:
            bound = self.run_environment(run_id)
            if bound != environment_id:
                raise EnvironmentStateError(
                    "USAGE_ENVIRONMENT_MISMATCH",
                    f"run={run_id} bound={bound} requested={environment_id}",
                )
        if session_id is not None:
            with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
                row = conn.execute(
                    "SELECT environment_id FROM amodex_sessions WHERE session_id=%s",
                    (session_id,),
                ).fetchone()
            if row is None or str(row["environment_id"]) != environment_id:
                raise EnvironmentStateError("SESSION_ENVIRONMENT_MISMATCH", session_id)

        usage_id = new_uuid7()
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                """
                INSERT INTO amodex_environment_usage_events(
                    usage_id,environment_id,session_id,run_id,task_id,
                    provider,model,input_tokens,output_tokens,requests,outcome,created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                """,
                (
                    usage_id,
                    environment_id,
                    session_id,
                    run_id,
                    task_id,
                    provider,
                    model,
                    input_tokens,
                    output_tokens,
                    requests,
                    outcome,
                ),
            )
        return usage_id

    def usage_summary(self, *, environment_id: str) -> dict[str, int]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(input_tokens),0)::bigint AS input_tokens,
                    COALESCE(SUM(output_tokens),0)::bigint AS output_tokens,
                    COALESCE(SUM(requests),0)::bigint AS requests
                FROM amodex_environment_usage_events
                WHERE environment_id=%s
                """,
                (environment_id,),
            ).fetchone()
        assert row is not None
        return {
            "input_tokens": int(row["input_tokens"]),
            "output_tokens": int(row["output_tokens"]),
            "requests": int(row["requests"]),
        }

    def upsert_budget_policy(self, *, environment_id: str, policy: dict[str, Any]) -> None:
        if self.get_environment(environment_id) is None:
            raise KeyError(environment_id)
        payload = json.dumps(policy, sort_keys=True, separators=(",", ":"))
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                """
                INSERT INTO amodex_environment_budget_policies(environment_id,policy,updated_at)
                VALUES (%s,%s::jsonb,NOW())
                ON CONFLICT(environment_id) DO UPDATE
                SET policy=EXCLUDED.policy, updated_at=NOW()
                """,
                (environment_id, payload),
            )

    def get_budget_policy(self, *, environment_id: str) -> dict[str, Any] | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT policy FROM amodex_environment_budget_policies WHERE environment_id=%s",
                (environment_id,),
            ).fetchone()
        return dict(row["policy"]) if row else None


def _validate_memory_binding(
    *,
    scope: MemoryScope,
    environment_id: str | None,
    session_id: str | None,
    domain_key: str | None,
) -> None:
    if scope == MemoryScope.OWNER:
        valid = environment_id is None and session_id is None and domain_key is None
    elif scope == MemoryScope.ENVIRONMENT:
        valid = environment_id is not None and session_id is None and domain_key is None
    elif scope == MemoryScope.DOMAIN:
        valid = environment_id is not None and session_id is None and bool(domain_key)
    elif scope == MemoryScope.SESSION:
        valid = environment_id is not None and session_id is not None
    else:
        valid = False
    if not valid:
        raise ValueError(f"invalid binding for memory scope {scope.value}")


def _environment_from_row(row: dict[str, Any]) -> EnvironmentRecord:
    return EnvironmentRecord(
        environment_id=str(row["environment_id"]),
        name=row["name"],
        engine=row["engine"],
        status=EnvironmentStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _session_from_row(row: dict[str, Any]) -> SessionRecord:
    return SessionRecord(
        session_id=str(row["session_id"]),
        environment_id=str(row["environment_id"]),
        title=row["title"],
        domain_key=row["domain_key"],
        status=SessionStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _memory_from_row(row: dict[str, Any]) -> MemoryRecord:
    return MemoryRecord(
        memory_id=str(row["memory_id"]),
        scope=MemoryScope(row["scope"]),
        content=row["content"],
        source_ref=row["source_ref"],
        content_sha256=row["content_sha256"],
        created_at=row["created_at"],
        environment_id=str(row["environment_id"]) if row["environment_id"] is not None else None,
        session_id=str(row["session_id"]) if row["session_id"] is not None else None,
        domain_key=row["domain_key"],
    )
