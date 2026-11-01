"""Durable Step 4 model benchmark, lifecycle and routing-decision storage."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from amodex.contracts.ids import new_uuid7
from amodex.model_intelligence.models import AttemptStatus, DecisionStatus
from amodex.providers.capabilities import ModelCandidate, ModelLifecycle


class PostgresModelIntelligenceRepository:
    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    def schema_ready(self) -> bool:
        with psycopg.connect(self._dsn) as conn:
            row = conn.execute(
                """
                SELECT
                  to_regclass('public.amodex_model_routes'),
                  to_regclass('public.amodex_model_benchmark_runs'),
                  to_regclass('public.amodex_model_route_decisions')
                """
            ).fetchone()
        return bool(row and all(row))

    def sync_candidates(self, candidates: tuple[ModelCandidate, ...]) -> None:
        with psycopg.connect(self._dsn) as conn:
            with conn.transaction():
                for item in candidates:
                    conn.execute(
                        """
                        INSERT INTO amodex_model_routes(
                            route_id,provider,model,credential_pool_id,cost_class,capabilities,
                            context_capacity_tokens,lifecycle,provenance,enabled,created_at,updated_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
                        ON CONFLICT (route_id) DO UPDATE SET
                            provider=EXCLUDED.provider,
                            model=EXCLUDED.model,
                            credential_pool_id=EXCLUDED.credential_pool_id,
                            cost_class=EXCLUDED.cost_class,
                            capabilities=EXCLUDED.capabilities,
                            context_capacity_tokens=EXCLUDED.context_capacity_tokens,
                            provenance=EXCLUDED.provenance,
                            enabled=EXCLUDED.enabled,
                            updated_at=NOW()
                        """,
                        (
                            item.route_id,
                            item.provider,
                            item.model,
                            item.credential_pool_id,
                            item.cost_class.value,
                            Jsonb(sorted(cap.value for cap in item.capabilities)),
                            item.context_capacity_tokens,
                            item.lifecycle.value,
                            item.provenance,
                            item.enabled,
                        ),
                    )

    def list_routes(self) -> tuple[dict[str, Any], ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT * FROM amodex_model_routes ORDER BY provider,model,credential_pool_id"
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def get_route(self, route_id: str) -> dict[str, Any] | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute("SELECT * FROM amodex_model_routes WHERE route_id=%s", (route_id,)).fetchone()
        return dict(row) if row else None

    def set_lifecycle(
        self,
        *,
        route_id: str,
        lifecycle: ModelLifecycle,
        reason: str,
        actor: str,
        evidence_ref: str | None,
    ) -> dict[str, Any]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                current = conn.execute(
                    "SELECT lifecycle FROM amodex_model_routes WHERE route_id=%s FOR UPDATE",
                    (route_id,),
                ).fetchone()
                if current is None:
                    raise KeyError(route_id)
                before = str(current["lifecycle"])
                row = conn.execute(
                    """
                    UPDATE amodex_model_routes SET lifecycle=%s,updated_at=NOW()
                    WHERE route_id=%s RETURNING *
                    """,
                    (lifecycle.value, route_id),
                ).fetchone()
                if before != lifecycle.value:
                    conn.execute(
                        """
                        INSERT INTO amodex_model_lifecycle_events(
                            lifecycle_event_id,route_id,from_lifecycle,to_lifecycle,reason,actor,evidence_ref,created_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())
                        """,
                        (new_uuid7(), route_id, before, lifecycle.value, reason, actor, evidence_ref),
                    )
        assert row is not None
        return dict(row)

    def list_lifecycle_events(self, *, route_id: str | None = None) -> tuple[dict[str, Any], ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            if route_id:
                rows = conn.execute(
                    "SELECT * FROM amodex_model_lifecycle_events WHERE route_id=%s ORDER BY created_at DESC",
                    (route_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM amodex_model_lifecycle_events ORDER BY created_at DESC"
                ).fetchall()
        return tuple(dict(row) for row in rows)

    def start_benchmark(self, *, route_id: str, suite_version: str, provenance: dict[str, Any]) -> str:
        benchmark_id = new_uuid7()
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                """
                INSERT INTO amodex_model_benchmark_runs(
                    benchmark_id,route_id,suite_version,status,provenance,started_at
                ) VALUES (%s,%s,%s,'STARTED',%s,NOW())
                """,
                (benchmark_id, route_id, suite_version, Jsonb(provenance)),
            )
        return benchmark_id

    def record_benchmark_case(
        self,
        *,
        benchmark_id: str,
        case_id: str,
        capability: str,
        score: float,
        passed: bool,
        latency_ms: float,
        total_tokens: int | None,
        result_excerpt: str | None,
        evidence: dict[str, Any],
    ) -> None:
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                """
                INSERT INTO amodex_model_benchmark_cases(
                    benchmark_case_id,benchmark_id,case_id,capability,score,passed,
                    latency_ms,total_tokens,result_excerpt,evidence,created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                """,
                (
                    new_uuid7(), benchmark_id, case_id, capability, score, passed,
                    latency_ms, total_tokens, result_excerpt, Jsonb(evidence),
                ),
            )

    def finish_benchmark(
        self,
        *,
        benchmark_id: str,
        status: str,
        sample_count: int,
        passed_count: int,
        aggregate_score: float | None,
        latency_ms_p50: float | None,
        total_tokens: int | None,
        progress_per_1k_tokens: float | None,
        error_code: str | None,
        error_message: str | None,
    ) -> dict[str, Any]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                UPDATE amodex_model_benchmark_runs SET
                    status=%s,sample_count=%s,passed_count=%s,aggregate_score=%s,
                    latency_ms_p50=%s,total_tokens=%s,progress_per_1k_tokens=%s,
                    error_code=%s,error_message=%s,completed_at=NOW()
                WHERE benchmark_id=%s RETURNING *
                """,
                (
                    status, sample_count, passed_count, aggregate_score, latency_ms_p50,
                    total_tokens, progress_per_1k_tokens, error_code, error_message, benchmark_id,
                ),
            ).fetchone()
        if row is None:
            raise KeyError(benchmark_id)
        return dict(row)

    def latest_benchmark(self, route_id: str) -> dict[str, Any] | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                SELECT * FROM amodex_model_benchmark_runs
                WHERE route_id=%s ORDER BY started_at DESC,benchmark_id DESC LIMIT 1
                """,
                (route_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_benchmarks(self, *, route_id: str | None = None, limit: int = 100) -> tuple[dict[str, Any], ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            if route_id:
                rows = conn.execute(
                    """SELECT * FROM amodex_model_benchmark_runs
                    WHERE route_id=%s ORDER BY started_at DESC LIMIT %s""",
                    (route_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM amodex_model_benchmark_runs ORDER BY started_at DESC LIMIT %s",
                    (limit,),
                ).fetchall()
        return tuple(dict(row) for row in rows)

    def list_benchmark_cases(self, benchmark_id: str) -> tuple[dict[str, Any], ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT * FROM amodex_model_benchmark_cases WHERE benchmark_id=%s ORDER BY created_at,case_id",
                (benchmark_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def benchmark_efficiency(self, route_id: str) -> float:
        row = self.latest_benchmark(route_id)
        if not row or row["status"] != "SUCCEEDED":
            return 0.0
        return float(row.get("progress_per_1k_tokens") or 0.0)

    def get_decision_for_owner_message(self, owner_message_id: str) -> dict[str, Any] | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT * FROM amodex_model_route_decisions WHERE owner_message_id=%s",
                (owner_message_id,),
            ).fetchone()
        return dict(row) if row else None

    def create_decision(
        self,
        *,
        chat_id: str,
        owner_message_id: str,
        profile_id: str,
        selected_route_id: str,
        ranked_route_ids: list[str],
        rejected_reasons: list[str],
        selection_facts: list[str],
    ) -> str:
        decision_id = new_uuid7()
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                """
                INSERT INTO amodex_model_route_decisions(
                    decision_id,chat_id,owner_message_id,profile_id,status,selected_route_id,
                    ranked_route_ids,rejected_reasons,selection_facts,attempt_count,fallback_used,
                    created_at,updated_at
                ) VALUES (%s,%s,%s,%s,'STARTED',%s,%s,%s,%s,0,FALSE,NOW(),NOW())
                """,
                (
                    decision_id, chat_id, owner_message_id, profile_id, selected_route_id,
                    Jsonb(ranked_route_ids), Jsonb(rejected_reasons), Jsonb(selection_facts),
                ),
            )
        return decision_id

    def start_attempt(
        self,
        *,
        decision_id: str,
        route_id: str,
        attempt_number: int,
        request_key: str,
    ) -> str:
        attempt_id = new_uuid7()
        with psycopg.connect(self._dsn) as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO amodex_model_route_attempts(
                        attempt_id,decision_id,route_id,attempt_number,request_key,status,started_at
                    ) VALUES (%s,%s,%s,%s,%s,'STARTED',NOW())
                    """,
                    (attempt_id, decision_id, route_id, attempt_number, request_key),
                )
                conn.execute(
                    "UPDATE amodex_model_route_decisions SET attempt_count=%s,updated_at=NOW() WHERE decision_id=%s",
                    (attempt_number, decision_id),
                )
        return attempt_id

    def finish_attempt(
        self,
        *,
        attempt_id: str,
        status: AttemptStatus,
        latency_ms: float | None,
        total_tokens: int | None,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                """
                UPDATE amodex_model_route_attempts SET
                    status=%s,latency_ms=%s,total_tokens=%s,error_code=%s,error_message=%s,completed_at=NOW()
                WHERE attempt_id=%s
                """,
                (status.value, latency_ms, total_tokens, error_code, error_message, attempt_id),
            )

    def finish_decision(
        self,
        *,
        decision_id: str,
        status: DecisionStatus,
        final_route_id: str | None,
        attempt_count: int,
        fallback_used: bool,
        result_message_id: str | None,
        error_code: str | None,
        error_message: str | None,
    ) -> dict[str, Any]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                UPDATE amodex_model_route_decisions SET
                    status=%s,final_route_id=%s,attempt_count=%s,fallback_used=%s,
                    result_message_id=%s,error_code=%s,error_message=%s,updated_at=NOW()
                WHERE decision_id=%s RETURNING *
                """,
                (
                    status.value, final_route_id, attempt_count, fallback_used,
                    result_message_id, error_code, error_message, decision_id,
                ),
            ).fetchone()
        if row is None:
            raise KeyError(decision_id)
        return dict(row)

    def list_decisions(self, *, chat_id: str, limit: int = 100) -> tuple[dict[str, Any], ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                """
                SELECT * FROM amodex_model_route_decisions
                WHERE chat_id=%s ORDER BY created_at DESC LIMIT %s
                """,
                (chat_id, limit),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def recent_failed_attempts(self, *, route_id: str, since: datetime) -> int:
        with psycopg.connect(self._dsn) as conn:
            row = conn.execute(
                """
                SELECT count(*) FROM amodex_model_route_attempts
                WHERE route_id=%s AND started_at >= %s AND status='FAILED'
                """,
                (route_id, since),
            ).fetchone()
        return int(row[0]) if row else 0

    def reconcile_stale_decisions(self, *, cutoff: datetime) -> int:
        with psycopg.connect(self._dsn) as conn:
            with conn.transaction():
                attempt_rows = conn.execute(
                    """
                    UPDATE amodex_model_route_attempts SET
                        status='UNKNOWN_OUTCOME',error_code='STALE_STARTED',
                        error_message='reconciled after local crash/interruption',completed_at=NOW()
                    WHERE status='STARTED' AND started_at < %s
                    RETURNING decision_id
                    """,
                    (cutoff,),
                ).fetchall()
                decision_rows = conn.execute(
                    """
                    UPDATE amodex_model_route_decisions SET
                        status='UNKNOWN_OUTCOME',error_code='STALE_STARTED',
                        error_message='reconciled after local crash/interruption',updated_at=NOW()
                    WHERE status='STARTED' AND updated_at < %s
                    RETURNING decision_id
                    """,
                    (cutoff,),
                ).fetchall()
                decision_ids = {
                    *(str(row[0]) for row in attempt_rows),
                    *(str(row[0]) for row in decision_rows),
                }
        return len(decision_ids)
