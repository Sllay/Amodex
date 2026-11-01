from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from amodex.providers.capabilities import (
    BillingClass,
    ModelMeasurement,
    ProviderAvailability,
    ProviderHealth,
)
from amodex.storage.routing import PostgresRoutingRepository


DSN = os.environ.get("AMODEX_TEST_DATABASE_URL", "")


def _measurement(observed_at: datetime, *, coding: float) -> ModelMeasurement:
    return ModelMeasurement(
        provider="groq",
        model="openai/gpt-oss-20b",
        sample_count=12,
        structured_tool_reliability=0.91,
        coding_pass_rate=coding,
        correction_success_rate=0.88,
        planning_schema_compliance=0.90,
        research_success_rate=0.82,
        review_detection_rate=0.78,
        vision_success_rate=None,
        latency_ms_p50=180.0,
        observed_at=observed_at,
        source=f"evaluation-{observed_at.isoformat()}",
    )


@pytest.mark.skipif(not DSN, reason="AMODEX_TEST_DATABASE_URL is required")
def test_latest_measurement_and_quota_state_survive_process_boundaries() -> None:
    repo = PostgresRoutingRepository(DSN)
    repo.migrate(Path("migrations/stage5/0001_routing_measurements.sql"))
    now = datetime.now(timezone.utc)
    older = _measurement(now - timedelta(minutes=5), coding=0.70)
    newer = _measurement(now, coding=0.93)
    repo.record_measurement(older)
    repo.record_measurement(newer)

    repo.record_availability(
        ProviderAvailability(
            provider="groq",
            credential_pool_id="groq-free-primary",
            billing_class=BillingClass.UNKNOWN,
            health=ProviderHealth.LIMITED,
            quota_available=False,
            free_capacity_ratio=0.0,
            observed_at=now - timedelta(minutes=5),
            reason="credential class not yet confirmed",
        )
    )
    repo.record_availability(
        ProviderAvailability(
            provider="groq",
            credential_pool_id="groq-free-primary",
            billing_class=BillingClass.FREE,
            health=ProviderHealth.HEALTHY,
            quota_available=True,
            free_capacity_ratio=0.75,
            observed_at=now,
            reason="fresh quota observation",
        )
    )

    reconstructed = PostgresRoutingRepository(DSN)
    measurements = reconstructed.latest_measurements()
    availability = reconstructed.latest_availability()

    groq_measurement = next(item for item in measurements if item.provider == "groq")
    groq_availability = next(item for item in availability if item.provider == "groq")
    assert groq_measurement.coding_pass_rate == 0.93
    assert groq_measurement.observed_at == newer.observed_at
    assert groq_availability.billing_class == BillingClass.FREE
    assert groq_availability.quota_available is True

    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name IN (
                    'amodex_model_measurements',
                    'amodex_provider_availability_observations'
                )
                """
            )
            columns = {row[0] for row in cur.fetchall()}
    assert not any(token in column.lower() for column in columns for token in ("secret", "api_key", "token_value"))
