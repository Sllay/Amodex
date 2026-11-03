from __future__ import annotations

import pytest

from amodex.model_intelligence.models import ModelIntelligenceError
from amodex.model_intelligence.vision_benchmark_runner import vision_benchmark_evidence
from amodex.model_intelligence.vision_benchmarks import VISION_SUITE_VERSION


class _SavedEvidenceRepository:
    def __init__(
        self,
        *,
        suite_version: str = VISION_SUITE_VERSION,
        complete: bool = True,
    ) -> None:
        self.provider_calls = 0
        self.complete = complete
        self.run = {
            "benchmark_id": "vision-saved-1",
            "route_id": "groq:qwen/qwen3.8-27b:groq-free-primary",
            "suite_version": suite_version,
            "status": "SUCCEEDED",
            "sample_count": 5 if complete else 3,
            "passed_count": 4 if complete else 1,
            "aggregate_score": 0.88 if complete else 0.514,
        }

    def list_benchmarks(self, *, route_id, limit):
        assert route_id is None
        assert limit == 500
        return (self.run,)

    def list_benchmark_cases(self, benchmark_id: str):
        assert benchmark_id == "vision-saved-1"
        if not self.complete:
            return (
                {
                    "case_id": "vision-position-color-v1",
                    "score": 0.5,
                    "passed": False,
                    "latency_ms": 120.0,
                    "result_excerpt": '{"blue_square":"top-right","red_circle":"top-left"}',
                    "evidence": {"field_results": {"red_circle": True, "blue_square": False}},
                },
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
                    "score": 0.0,
                    "passed": False,
                    "latency_ms": 140.0,
                    "result_excerpt": None,
                    "evidence": {
                        "provider_failure": "RATE_LIMITED",
                        "retry_after_seconds": 8.0,
                    },
                },
            )

        case_ids = (
            "vision-position-color-v1",
            "vision-ocr-v1",
            "vision-count-v1",
            "vision-direction-v1",
            "vision-spatial-relation-v1",
        )
        rows = []
        for index, case_id in enumerate(case_ids):
            passed = index < 4
            rows.append(
                {
                    "case_id": case_id,
                    "score": 1.0 if passed else 0.4,
                    "passed": passed,
                    "latency_ms": 120.0 + index,
                    "result_excerpt": '{"proof":true}',
                    "evidence": {
                        "scored": True,
                        "attempts": 1,
                        "field_results": {"proof": passed},
                    },
                }
            )
        return tuple(rows)


class _SavedEvidenceService:
    def __init__(self, repo: _SavedEvidenceRepository) -> None:
        self._repository = repo


def test_saved_complete_visual_evidence_is_replayed_without_provider_call() -> None:
    repo = _SavedEvidenceRepository()
    result = vision_benchmark_evidence(
        _SavedEvidenceService(repo), benchmark_id="vision-saved-1"
    )

    assert result["replayed_from_storage"] is True
    assert result["provider_call_performed"] is False
    assert result["status"] == "SUCCEEDED"
    assert result["suite_complete"] is True
    assert result["vision_measured"] is True
    assert result["vision_success_rate"] == 0.88
    assert result["pass_rate"] == 0.8
    assert result["promotion_candidate"] is True
    assert result["scored_case_count"] == 5
    assert len(result["cases"]) == 5
    assert repo.provider_calls == 0


def test_legacy_partial_success_is_replayed_as_blocked_unmeasured_evidence() -> None:
    repo = _SavedEvidenceRepository(complete=False)
    result = vision_benchmark_evidence(
        _SavedEvidenceService(repo), benchmark_id="vision-saved-1"
    )

    assert result["stored_status"] == "SUCCEEDED"
    assert result["status"] == "BLOCKED"
    assert result["suite_complete"] is False
    assert result["vision_measured"] is False
    assert result["vision_success_rate"] is None
    assert result["partial_score"] == 0.514
    assert result["attempted_case_count"] == 3
    assert result["scored_case_count"] == 2
    assert result["cases"][-1]["provider_failure"] == "RATE_LIMITED"
    assert result["cases"][-1]["scored"] is False
    assert result["promotion_candidate"] is False


def test_text_benchmark_cannot_be_replayed_as_visual_evidence() -> None:
    repo = _SavedEvidenceRepository(suite_version="step4-live-v1")
    with pytest.raises(ModelIntelligenceError) as raised:
        vision_benchmark_evidence(_SavedEvidenceService(repo), benchmark_id="vision-saved-1")
    assert raised.value.code == "VISION_BENCHMARK_NOT_FOUND"
