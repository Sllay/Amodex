from __future__ import annotations

from amodex.benchmarks.stage5_routing import run_stage5_vertical_benchmark


def test_specialized_vertical_slice_improves_without_safety_regression() -> None:
    report = run_stage5_vertical_benchmark()

    assert report.routes["research"].startswith("openrouter:")
    assert report.routes["implementation"].startswith("groq:")
    assert report.routes["correction"].startswith("groq:")
    assert report.routes["review"].startswith("nvidia:")

    assert report.specialized_quality > report.baseline_quality
    assert report.specialized_wall_ms < report.baseline_wall_ms
    assert report.specialized_requests == report.baseline_requests
    assert report.specialized_quota_requests == report.baseline_quota_requests
    assert report.specialized_context_chars < report.baseline_context_chars
    assert report.context_reduction_ratio > 0.45

    assert "contract" in report.reviewer_channels
    assert "diff" in report.reviewer_channels
    assert "evidence" in report.reviewer_channels
    assert "implementation-transcript" not in report.reviewer_channels
    assert set(report.final_states.values()) == {"SUCCEEDED"}
