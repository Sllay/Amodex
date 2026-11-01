from __future__ import annotations

from amodex.benchmarks.environment_budget_stress import run_stress


def test_environment_budget_stress_keeps_progress_high_without_cross_context_or_paid_fallback() -> None:
    metrics = run_stress(task_count=400)
    assert metrics.completed_tasks == 400
    assert metrics.blocked_tasks == 0
    assert metrics.cross_environment_blocks == 400
    assert metrics.paid_route_selections == 0
    assert metrics.provider_failover_events == 2
    assert metrics.provider_selections["gemini"] > 0
    assert metrics.provider_selections["nvidia"] > 0
    assert metrics.tasks_per_million_tokens > 20
