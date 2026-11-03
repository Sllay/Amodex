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
from amodex.providers.base import ProviderFailure
from amodex.providers.capabilities import (
    BillingClass,
    ModelCandidate,
    ModelCapability,
    ModelLifecycle,
)
from amodex.providers.catalog import stage5_declared_candidates
from amodex.providers.groq import GroqProvider
from amodex.providers.mistral import MistralProvider
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


def test_catalog_declares_qwen_and_mistral_large4_as_pending_free_vision_routes() -> None:
    candidates = stage5_declared_candidates()
    qwen = next(item for item in candidates if item.model == "qwen/qwen3.8-27b")
    assert qwen.provider == "groq"
    assert qwen.cost_class == BillingClass.FREE
    assert qwen.lifecycle == ModelLifecycle.BENCHMARK_PENDING
    assert ModelCapability.VISION in qwen.capabilities
    assert qwen.context_capacity_tokens == 131_072

    mistral = next(
        item for item in candidates if item.provider == "mistral" and item.model == "mistral-large-4"
    )
    assert mistral.credential_pool_id == "mistral-free-primary"
    assert mistral.cost_class == BillingClass.FREE
    assert mistral.lifecycle == ModelLifecycle.BENCHMARK_PENDING
    assert ModelCapability.VISION in mistral.capabilities
    assert ModelCapability.LONG_CONTEXT in mistral.capabilities
    assert mistral.context_capacity_tokens == 1_000_000


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
    result = GroqProvider(api_key="test-key").generate_with_image(
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


def test_mistral_large4_vision_transport_uses_native_data_url_shape(monkeypatch) -> None:
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
                "choices": [{"message": {"content": '{"text":"AMODEX 4827"}'}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 12, "total_tokens": 912},
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    image = vision_benchmark_cases()[1].image_png
    result = MistralProvider(api_key="mistral-test-key").generate_with_image(
        model="mistral-large-4",
        prompt="Read the image and return JSON.",
        image_data=image,
        media_type="image/png",
        request_key="benchmark:vision:mistral-test",
    )

    payload = observed["json"]
    assert isinstance(payload, dict)
    assert observed["url"] == "https://api.mistral.ai/v1/chat/completions"
    assert payload["model"] == "mistral-large-4"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["random_seed"] == 7
    assert payload["max_tokens"] == 256
    content = payload["messages"][0]["content"]
    assert content[1]["type"] == "image_url"
    data_url = content[1]["image_url"]
    assert isinstance(data_url, str)
    prefix = "data:image/png;base64,"
    assert data_url.startswith(prefix)
    assert base64.b64decode(data_url.removeprefix(prefix)) == image
    assert json.loads(result.text) == {"text": "AMODEX 4827"}
    assert result.usage.total_tokens == 912


class _FakeBenchmarkRepository:
    def __init__(self, candidate: ModelCandidate) -> None:
        self.candidate = candidate
        self.cases: list[dict[str, object]] = []
        self.finished: dict[str, object] | None = None

    def get_route(self, route_id: str):
        if route_id != self.candidate.route_id:
            return None
        return {"route_id": route_id, "lifecycle": self.candidate.lifecycle.value}

    def list_benchmarks(self, *, route_id: str | None, limit: int):
        return ()

    def start_benchmark(self, *, route_id: str, suite_version: str, provenance: dict):
        assert route_id == self.candidate.route_id
        assert suite_version == VISION_SUITE_VERSION
        assert provenance["automatic_promotion"] is False
        assert provenance["shared_provider_availability_mutation"] is False
        assert provenance["provider_failures_are_unscored"] is True
        return "vision-benchmark-id"

    def record_benchmark_case(self, **kwargs) -> None:
        self.cases.append(kwargs)

    def list_benchmark_cases(self, benchmark_id: str):
        assert benchmark_id == "vision-benchmark-id"
        return tuple(
            {
                "case_id": item["case_id"],
                "score": item["score"],
                "passed": item["passed"],
                "latency_ms": item["latency_ms"],
                "result_excerpt": item["result_excerpt"],
                "evidence": item["evidence"],
            }
            for item in self.cases
        )

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
        assert image_data.startswith(b"\x89PNG")
        assert media_type == "image/png"
        assert request_key.startswith("benchmark:vision:")
        return ProviderResponse(
            provider="fake",
            model=model,
            text=self.expected_by_prompt[prompt],
            usage=ProviderUsage(prompt_tokens=2048, completion_tokens=20, total_tokens=2068),
        )


class _RateLimitedAfterTwoClient(_FakeVisionClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def generate_with_image(self, **kwargs):
        self.calls += 1
        if self.calls >= 3:
            raise ProviderFailure(
                code="RATE_LIMITED",
                message="test rate limit",
                retry_after_seconds=0.0,
            )
        return super().generate_with_image(**kwargs)


class _FakeRegistry:
    def __init__(self, client) -> None:
        self.client = client

    def client_for(self, candidate):
        assert candidate.cost_class == BillingClass.FREE
        return self.client


class _FakeService:
    def __init__(self, candidate: ModelCandidate, client=None) -> None:
        self._repository = _FakeBenchmarkRepository(candidate)
        self._routing_repository = _FakeRoutingRepository()
        self._execution_registry = _FakeRegistry(client or _FakeVisionClient())
        self._benchmark_cooldown = timedelta(minutes=30)
        self._candidate = candidate

    def _candidate_from_row(self, _row):
        return self._candidate

    @staticmethod
    def _utcnow():
        return datetime.now(timezone.utc)

    @staticmethod
    def _quota_failure(exc):
        return exc.code in {"RATE_LIMITED", "QUOTA_EXHAUSTED_WINDOW"}


def test_vision_runner_measures_all_cases_without_promoting_or_throttling_text() -> None:
    candidate = next(item for item in stage5_declared_candidates() if item.model == "qwen/qwen3.8-27b")
    service = _FakeService(candidate)
    result = asyncio.run(run_vision_benchmark(service, route_id=candidate.route_id))

    assert result["status"] == "SUCCEEDED"
    assert result["vision_measured"] is True
    assert result["suite_complete"] is True
    assert result["vision_success_rate"] == 1.0
    assert result["pass_rate"] == 1.0
    assert result["promotion_candidate"] is True
    assert result["promotion_applied"] is False
    assert result["shared_provider_availability_mutated"] is False
    assert result["lifecycle_unchanged"] == ModelLifecycle.BENCHMARK_PENDING.value
    assert result["expected_case_count"] == 5
    assert result["attempted_case_count"] == 5
    assert result["scored_case_count"] == 5
    assert len(result["cases"]) == 5
    assert all(item["passed"] is True for item in result["cases"])
    assert all(item["scored"] is True for item in result["cases"])
    assert all(item["result_excerpt"] for item in result["cases"])
    assert len(service._routing_repository.measurements) == 1
    assert service._routing_repository.measurements[0].vision_success_rate == 1.0
    assert service._routing_repository.availability == []


def test_rate_limit_blocks_incomplete_suite_without_poisoning_vision_score() -> None:
    candidate = next(item for item in stage5_declared_candidates() if item.model == "qwen/qwen3.8-27b")
    client = _RateLimitedAfterTwoClient()
    service = _FakeService(candidate, client=client)

    result = asyncio.run(run_vision_benchmark(service, route_id=candidate.route_id))

    assert result["status"] == "BLOCKED"
    assert result["vision_measured"] is False
    assert result["vision_success_rate"] is None
    assert result["suite_complete"] is False
    assert result["promotion_candidate"] is False
    assert result["retryable"] is True
    assert result["scored_case_count"] == 2
    assert result["attempted_case_count"] == 3
    assert result["expected_case_count"] == 5
    assert len(result["cases"]) == 3
    blocked_case = result["cases"][-1]
    assert blocked_case["provider_failure"] == "RATE_LIMITED"
    assert blocked_case["scored"] is False
    assert blocked_case["attempts"] == 2
    assert service._repository.finished is not None
    assert service._repository.finished["status"] == "BLOCKED"
    assert service._repository.finished["sample_count"] == 2
    assert service._routing_repository.measurements == []
    assert service._routing_repository.availability == []
