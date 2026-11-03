from __future__ import annotations

import pytest

from amodex.model_intelligence.models import ModelIntelligenceError
from amodex.model_intelligence.vision_benchmark_runner import vision_benchmark_evidence
from amodex.model_intelligence.vision_benchmarks import VISION_SUITE_VERSION


class _SavedEvidenceRepository:
    def __init__(self, *, suite_version: str = VISION_SUITE_VERSION) -> None:
        self.provider_calls = 0
        self.run = {
            "benchmark_id": "vision-saved-1",
            "route_id": "groq:qwen/qwen3.8-27b:groq-free-primary",
            "suite_version": suite_version,
            "status": "SUCCEEDED",
            "sample_count": 5,
            "passed_count": 4,
            "aggregate_score": 0.88,
        }

    def list_benchmarks(self, *, route_id, limit):
        assert route_id is None
        assert limit == 500
        return (self.run,)

    def list_benchmark_cases(self, benchmark_id: str):
        assert benchmark_id == "vision-saved-1"
        return (
            {
                "case_id": "vision-ocr-v1",
                "score": 1.0,
                "passed": True,
                "latency_ms": 123.0,
                "result_excerpt": '{"text":"AMODEX 4827"}',
                "evidence": {"field_results": {"text": True}},
            },
            {
                "case_id": "vision-count-v1",
                "score": 0.5,
                "passed": False,
                "latency_ms": 140.0,
                "result_excerpt": '{"green_triangles":3,"orange_circles":3}',
                "evidence": {
                    "field_results": {"green_triangles": True, "orange_circles": False}
                },
            },
        )


class _SavedEvidenceService:
    def __init__(self, repo: _SavedEvidenceRepository) -> None:
        self._repository = repo


def test_saved_visual_evidence_is_replayed_without_provider_call() -> None:
    repo = _SavedEvidenceRepository()
    result = vision_benchmark_evidence(
        _SavedEvidenceService(repo), benchmark_id="vision-saved-1"
    )

    assert result["replayed_from_storage"] is True
    assert result["provider_call_performed"] is False
    assert result["vision_success_rate"] == 0.88
    assert result["pass_rate"] == 0.8
    assert result["promotion_candidate"] is True
    assert len(result["cases"]) == 2
    assert result["cases"][0]["result_excerpt"] == '{"text":"AMODEX 4827"}'
    assert result["cases"][1]["field_results"]["orange_circles"] is False
    assert repo.provider_calls == 0


def test_text_benchmark_cannot_be_replayed_as_visual_evidence() -> None:
    repo = _SavedEvidenceRepository(suite_version="step4-live-v1")
    with pytest.raises(ModelIntelligenceError) as raised:
        vision_benchmark_evidence(_SavedEvidenceService(repo), benchmark_id="vision-saved-1")
    assert raised.value.code == "VISION_BENCHMARK_NOT_FOUND"
