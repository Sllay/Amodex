from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json

import httpx

from amodex.model_intelligence.vision_benchmark_runner import run_vision_benchmark
from amodex.model_intelligence.vision_benchmarks import (
    VISION_SUITE_VERSION,
    score_vision_case,
    vision_benchmark_cases,
)
from amodex.providers.capabilities import (
    BillingClass,
    ModelCandidate,
    ModelCapability,
    ModelLifecycle,
)
from amodex.providers.catalog import stage5_declared_candidates
from amodex.providers.groq import GroqProvider
from amodex.runtime.models import ProviderResponse, ProviderUsage


def test_vision_fixtures_are_deterministic_unique_pngs() -> None:
    first = vision_benchmark_cases()
    second = vision_benchmark_cases()
    assert len(first) == 5
    assert [case.case_id for case in first] == [case.case_id for case in second]
    assert [hashlib.sha256(case.image_png).hexdigest() for case in first] == [
        hashlib.sha256(case.image_png).hexdigest() for case in second
    ]
    assert len({hashlib.sha256(case.image_png).hexdigest() for case in first}) == 5
    assert all(case.image_png.startswith(b"\x89PNG\r\n\x1a\n") for case in first)
    assert all(case.capability == ModelCapability.VISION for case in first)


def test_vision_scorer_supports_exact_and_partial_field_credit() -> None:
    case = vision_benchmark_cases()[0]
    score, passed, evidence = score_vision_case(case, json.dumps(case.expected))
    assert score == 1.0
    assert passed is True
    assert evidence["matched_fields"] == len(case.expected)

    partial = dict(case.expected)
    key = next(iter(partial))
    partial[key] = "wrong"
    score, passed, evidence = score_vision_case(case, json.dumps(partial))
    assert score == 0.5
    assert passed is False
    assert evidence["matched_fields"] == 1


def test_catalog_declares_qwen_38_as_pending_free_vision_route() -> None:
    candidate = next(item for item in stage5_declared_candidates() if item.model == "qwen/qwen3.8-27b")
    assert candidate.provider == "groq"
    assert candidate.cost_class == BillingClass.FREE
    assert candidate.lifecycle == ModelLifecycle.BENCHMARK_PENDING
    assert ModelCapability.VISION in candidate.capabilities
    assert candidate.context_capacity_tokens == 131_072


def test_groq_vision_transport_sends_real_image_and_requires_json(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_post(url, *, headers, json, timeout):
        observed["url"] = url
        observed["headers"] = headers
        observed["json"] = json
        observed["timeout"] = timeout
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [{"message": {"content": '{"blue_square":"bottom-right","red_circle":"top-left"}'}}],
                "usage": {"prompt_tokens": 2100, "completion_tokens": 20, "total_tokens": 2120},
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    image = vision_benchmark_cases()[0].image_png
    provider = GroqProvider(api_key="test-key")
    result = provider.generate_with_image(
        model="qwen/qwen3.8-27b",
        prompt="Return the measured positions as JSON.",
        image_data=image,
        media_type="image/png",
        request_key="benchmark:vision:test",
    )

    payload = observed["json"]
    assert isinstance(payload, dict)
    assert observed["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert payload["model"] == "qwen/qwen3.8-27b"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["reasoning_effort"] == "none"
    content = payload["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert "Inspect the image pixels" in content[0]["text"]
    data_url = content[1]["image_url"]["url"]
    prefix = "data:image/png;base64,"
    assert data_url.startswith(prefix)
    assert base64.b64decode(data_url.removeprefix(prefix)) == image
    assert json.loads(result.text) == {
        "blue_square": "bottom-right",
        "red_circle": "top-left",
    }
    assert result.usage.total_tokens == 2120


class _FakeBenchmarkRepository:
    def __init__(self, candidate: ModelCandidate) -> None:
        self.candidate = candidate
        self.cases: list[dict[str, object]] = []
        self.finished: dict[str, object] | None = None

    def get_route(self, route_id: str):
        if route_id != self.candidate.route_id:
            return None
        return {"route_id": route_id, "lifecycle": self.candidate.lifecycle.value}

    def list_benchmarks(self, *, route_id: str, limit: int):
        return ()

    def start_benchmark(self, *, route_id: str, suite_version: str, provenance: dict):
        assert route_id == self.candidate.route_id
        assert suite_version == VISION_SUITE_VERSION
        assert provenance["automatic_promotion"] is False
        return "vision-benchmark-id"

    def record_benchmark_case(self, **kwargs) -> None:
        self.cases.append(kwargs)

    def finish_benchmark(self, **kwargs):
        self.finished = dict(kwargs)
        return {"benchmark_id": "vision-benchmark-id", **kwargs}


class _FakeRoutingRepository:
    def __init__(self) -> None:
        self.measurements = []
        self.availability = []

    def latest_measurements(self):
        return ()

    def record_measurement(self, measurement) -> None:
        self.measurements.append(measurement)

    def record_availability(self, availability) -> None:
        self.availability.append(availability)


class _FakeVisionClient:
    def __init__(self) -> None:
        self.expected_by_prompt = {
            case.prompt: json.dumps(case.expected, separators=(",", ":"))
            for case in vision_benchmark_cases()
        }

    def generate_with_image(self, *, model, prompt, image_data, media_type, request_key):
        assert model == "qwen/qwen3.8-27b"
        assert image_data.startswith(b"\x89PNG")
        assert media_type == "image/png"
        assert request_key.startswith("benchmark:vision:")
        return ProviderResponse(
            provider="groq",
            model=model,
            text=self.expected_by_prompt[prompt],
            usage=ProviderUsage(prompt_tokens=2048, completion_tokens=20, total_tokens=2068),
        )


class _FakeRegistry:
    def __init__(self, client) -> None:
        self.client = client

    def client_for(self, candidate):
        assert candidate.cost_class == BillingClass.FREE
        return self.client


class _FakeService:
    def __init__(self, candidate: ModelCandidate) -> None:
        self._repository = _FakeBenchmarkRepository(candidate)
        self._routing_repository = _FakeRoutingRepository()
        self._execution_registry = _FakeRegistry(_FakeVisionClient())
        self._benchmark_cooldown = timedelta(minutes=30)
        self._candidate = candidate

    def _candidate_from_row(self, _row):
        return self._candidate

    @staticmethod
    def _utcnow():
        return datetime.now(timezone.utc)

    @staticmethod
    def _quota_failure(_exc):
        return False


def test_vision_runner_measures_all_cases_without_promoting_lifecycle() -> None:
    candidate = next(item for item in stage5_declared_candidates() if item.model == "qwen/qwen3.8-27b")
    service = _FakeService(candidate)
    result = asyncio.run(run_vision_benchmark(service, route_id=candidate.route_id))

    assert result["vision_measured"] is True
    assert result["vision_success_rate"] == 1.0
    assert result["pass_rate"] == 1.0
    assert result["promotion_candidate"] is True
    assert result["promotion_applied"] is False
    assert result["lifecycle_unchanged"] == ModelLifecycle.BENCHMARK_PENDING.value
    assert len(service._repository.cases) == 5
    assert all(item["capability"] == ModelCapability.VISION.value for item in service._repository.cases)
    assert len(service._routing_repository.measurements) == 1
    assert service._routing_repository.measurements[0].vision_success_rate == 1.0
