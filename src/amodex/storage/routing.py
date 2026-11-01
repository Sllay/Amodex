"""PostgreSQL persistence for Stage 5 model-routing evidence.

Only metadata and measurements are stored here. Raw provider credentials never
enter this repository.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from amodex.contracts.ids import new_uuid7
from amodex.providers.capabilities import (
    BillingClass,
    ModelMeasurement,
    ProviderAvailability,
    ProviderHealth,
)


class PostgresRoutingRepository:
    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    def migrate(self, migration_path: str | Path) -> None:
        sql = Path(migration_path).read_text(encoding="utf-8")
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)

    def record_measurement(self, item: ModelMeasurement) -> str:
        measurement_id = new_uuid7()
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO amodex_model_measurements (
                        measurement_id, provider, model, sample_count,
                        structured_tool_reliability, coding_pass_rate,
                        correction_success_rate, planning_schema_compliance,
                        research_success_rate, review_detection_rate,
                        vision_success_rate, latency_ms_p50, observed_at, source
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        measurement_id,
                        item.provider,
                        item.model,
                        item.sample_count,
                        item.structured_tool_reliability,
                        item.coding_pass_rate,
                        item.correction_success_rate,
                        item.planning_schema_compliance,
                        item.research_success_rate,
                        item.review_detection_rate,
                        item.vision_success_rate,
                        item.latency_ms_p50,
                        item.observed_at,
                        item.source,
                    ),
                )
        return measurement_id

    def latest_measurements(self) -> tuple[ModelMeasurement, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (provider, model) *
                    FROM amodex_model_measurements
                    ORDER BY provider, model, observed_at DESC, measurement_id DESC
                    """
                )
                rows = cur.fetchall()
        return tuple(_measurement_from_row(row) for row in rows)

    def record_availability(self, item: ProviderAvailability) -> str:
        availability_id = new_uuid7()
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO amodex_provider_availability_observations (
                        availability_id, provider, credential_pool_id,
                        billing_class, health, quota_available, free_capacity_ratio,
                        observed_at, reason
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        availability_id,
                        item.provider,
                        item.credential_pool_id,
                        item.billing_class.value,
                        item.health.value,
                        item.quota_available,
                        item.free_capacity_ratio,
                        item.observed_at,
                        item.reason,
                    ),
                )
        return availability_id

    def latest_availability(self) -> tuple[ProviderAvailability, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (provider, credential_pool_id) *
                    FROM amodex_provider_availability_observations
                    ORDER BY provider, credential_pool_id, observed_at DESC, availability_id DESC
                    """
                )
                rows = cur.fetchall()
        return tuple(_availability_from_row(row) for row in rows)


def _measurement_from_row(row: dict[str, object]) -> ModelMeasurement:
    return ModelMeasurement(
        provider=str(row["provider"]),
        model=str(row["model"]),
        sample_count=int(row["sample_count"]),
        structured_tool_reliability=float(row["structured_tool_reliability"]),
        coding_pass_rate=float(row["coding_pass_rate"]),
        correction_success_rate=float(row["correction_success_rate"]),
        planning_schema_compliance=float(row["planning_schema_compliance"]),
        research_success_rate=float(row["research_success_rate"]),
        review_detection_rate=float(row["review_detection_rate"]),
        vision_success_rate=(
            None if row["vision_success_rate"] is None else float(row["vision_success_rate"])
        ),
        latency_ms_p50=float(row["latency_ms_p50"]),
        observed_at=row["observed_at"],  # type: ignore[arg-type]
        source=str(row["source"]),
    )


def _availability_from_row(row: dict[str, object]) -> ProviderAvailability:
    return ProviderAvailability(
        provider=str(row["provider"]),
        credential_pool_id=str(row["credential_pool_id"]),
        billing_class=BillingClass(str(row["billing_class"])),
        health=ProviderHealth(str(row["health"])),
        quota_available=bool(row["quota_available"]),
        free_capacity_ratio=float(row["free_capacity_ratio"]),
        observed_at=row["observed_at"],  # type: ignore[arg-type]
        reason=str(row["reason"]),
    )
