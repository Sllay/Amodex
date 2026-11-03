from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json

import httpx
import pytest

from amodex.model_intelligence.models import ModelIntelligenceError
from amodex.model_intelligence.vision_benchmark_runner import run_vision_benchmark
from amodex.model_intelligence.vision_benchmarks import VISION_SUITE_VERSION, score_vision_case, vision_benchmark_cases
from amodex.providers.base import ProviderFailure
from amodex.providers.capabilities import BillingClass, ModelCapability, ModelLifecycle
from amodex.providers.catalog import stage5_declared_candidates
from amodex.providers.groq import GroqProvider
from amodex.providers.nvidia import NvidiaProvider
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


def test_vision_scorer_supports_exact_and_partial_field_credit() -> None:
    case = vision_benchmark_cases()[0]
    score, passed, evidence = score_vision_case(case, json.dumps(case.expected))
    assert (score, passed) == (1.0, True)
    assert evidence["matched_fields"] == len(case.expected)
    partial = dict(case.expected)
    partial[next(iter(partial))] = "wrong"
    score, passed, _ = score_vision_case(case, json.dumps(partial))
    assert (score, passed) == (0.5, False)


def test_catalog_declares_only_real_hosted_mistral_vision_route() -> None:
    candidates = stage5_declared_candidates()
    assert not any(item.provider == "mistral" and item.model == "mistral-large-4" for item in candidates)

    qwen = next(item for item in candidates if item.model == "qwen/qwen3.8-27b")
    assert qwen.provider == "groq"
    assert qwen.cost_class == BillingClass.FREE
    assert qwen.lifecycle == ModelLifecycle.BENCHMARK_PENDING
    assert ModelCapability.VISION in qwen.capabilities

    mistral = next(
        item for item in candidates
        if item.provider == "nvidia" and item.model == "mistralai/mistral-large-4-1t-a52b"
    )
    assert mistral.credential_pool_id == "nvidia-free-primary"
    assert mistral.cost_class == BillingClass.FREE
    assert mistral.lifecycle == ModelLifecycle.BENCHMARK_PENDING
    assert ModelCapability.VISION in mistral.capabilities
    assert ModelCapability.LONG_CONTEXT in mistral.capabilities
    assert mistral.context_capacity_tokens == 262_144


def test_groq_vision_transport_still_sends_real_image(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_post(url, *, headers, json, timeout):
        observed.update(url=url, headers=headers, payload=json, timeout=timeout)
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [{"message": {"content": '{"blue_square":"bottom-right","red_circle":"top-left"}'}}],
                "usage": {"total_tokens": 2120},
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
    payload = observed["payload"]
    assert observed["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["reasoning_effort"] == "none"
    data_url = payload["messages"][0]["content"][1]["image_url"]["url"]
    assert base64.b64decode(data_url.removeprefix("data:image/png;base64,")) == image
    assert json.loads(result.text)["blue_square"] == "bottom-right"


def test_nvidia_mistral_vision_transport_matches_hosted_api_reference(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_post(url, *, headers, json, timeout):
        observed.update(url=url, headers=headers, payload=json, timeout=timeout)
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"text":"AMODEX 4827"}'}}],
                "usage": {"total_tokens": 912},
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    image = vision_benchmark_cases()[1].image_png
    result = NvidiaProvider(api_key="nvidia-test-key").generate_with_image(
        model="mistralai/mistral-large-4-1t-a52b",
        prompt="Read the image and return JSON.",
        image_data=image,
        media_type="image/png",
        request_key="benchmark:vision:nvidia-mistral",
    )

    payload = observed["payload"]
    assert observed["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert observed["headers"]["authorization"] == "Bearer nvidia-test-key"
    assert observed["headers"]["accept"] == "application/json"
    assert payload["model"] == "mistralai/mistral-large-4-1t-a52b"
    assert "response_format" not in payload
    assert payload["seed"] == 7
    assert payload["reasoning_effort"] == "high"
    assert payload["max_tokens"] == 4096
    assert payload["top_p"] == 1
    image_url = payload["messages"][0]["content"][1]["image_url"]
    assert isinstance(image_url, dict)
    assert base64.b64decode(image_url["url"].removeprefix("data:image/png;base64,")) == image
    assert json.loads(result.text) == {"text": "AMODEX 4827"}


def test_nvidia_reasoning_length_without_final_content_is_transport_failure(monkeypatch) -> None:
    def fake_post(url, *, headers, json, timeout):
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": None, "reasoning_content": "still reasoning"},
                    }
                ],
                "usage": {"total_tokens": 4096},
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(ProviderFailure) as exc_info:
        NvidiaProvider(api_key="nvidia-test-key").generate_with_image(
            model="mistralai/mistral-large-4-1t-a52b",
            prompt="Return JSON.",
            image_data=vision_benchmark_cases()[0].image_png,
            media_type="image/png",
            request_key="benchmark:vision:nvidia-length",
        )
    assert exc_info.value.code == "PROVIDER_OUTPUT_TRUNCATED"
    assert exc_info.value.http_status == 200


def test_nvidia_accepts_fenced_json_without_provider_json_mode(monkeypatch) -> None:
    def fake_post(url, *, headers, json, timeout):
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '```json\n{"text":"AMODEX 4827"}\n```'}}],
                "usage": {},
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    result = NvidiaProvider(api_key="nvidia-test-key").generate_with_image(
        model="mistralai/mistral-large-4-1t-a52b",
        prompt="Read image.",
        image_data=vision_benchmark_cases()[1].image_png,
        media_type="image/png",
        request_key="benchmark:vision:nvidia-fenced",
    )
    assert json.loads(result.text) == {"text": "AMODEX 4827"}


class _Repo:
    def __init__(self, candidate, *, enabled: bool = True):
        self.candidate = candidate
        self.enabled = enabled
        self.cases = []
        self.finished = None

    def get_route(self, route_id):
        return {
            "route_id": route_id,
            "lifecycle": self.candidate.lifecycle.value,
            "enabled": self.enabled,
        }

    def list_benchmarks(self, *, route_id, limit):
        return ()

    def start_benchmark(self, **kwargs):
        assert kwargs["suite_version"] == VISION_SUITE_VERSION
        return "vision-benchmark-id"

    def record_benchmark_case(self, **kwargs):
        self.cases.append(kwargs)

    def list_benchmark_cases(self, benchmark_id):
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
        self.finished = kwargs
        return {"benchmark_id": "vision-benchmark-id", **kwargs}


class _Routing:
    def __init__(self):
        self.measurements = []
        self.availability = []

    def latest_measurements(self):
        return ()

    def record_measurement(self, measurement):
        self.measurements.append(measurement)

    def record_availability(self, availability):
        self.availability.append(availability)


class _VisionClient:
    def __init__(self, fail_after: int | None = None, failure_code: str = "RATE_LIMITED"):
        self.expected = {case.prompt: json.dumps(case.expected, separators=(",", ":")) for case in vision_benchmark_cases()}
        self.calls = 0
        self.fail_after = fail_after
        self.failure_code = failure_code

    def generate_with_image(self, *, model, prompt, image_data, media_type, request_key):
        self.calls += 1
        if self.fail_after is not None and self.calls >= self.fail_after:
            raise ProviderFailure(
                code=self.failure_code,
                message="test provider failure",
                retry_after_seconds=0.0,
                http_status=200 if self.failure_code == "MALFORMED_PROVIDER_RESPONSE" else 429,
            )
        return ProviderResponse(
            provider="fake",
            model=model,
            text=self.expected[prompt],
            usage=ProviderUsage(total_tokens=2068),
        )


class _Registry:
    def __init__(self, client):
        self.client = client

    def client_for(self, candidate):
        assert candidate.cost_class == BillingClass.FREE
        return self.client


class _Service:
    def __init__(self, candidate, client, *, enabled: bool = True):
        self._repository = _Repo(candidate, enabled=enabled)
        self._routing_repository = _Routing()
        self._execution_registry = _Registry(client)
        self._benchmark_cooldown = timedelta(minutes=30)
        self._candidate = candidate

    def _candidate_from_row(self, _row):
        return self._candidate

    @staticmethod
    def _utcnow():
        return datetime.now(timezone.utc)


def test_complete_suite_records_vision_measurement_without_auto_promotion() -> None:
    candidate = next(item for item in stage5_declared_candidates() if item.model == "qwen/qwen3.8-27b")
    service = _Service(candidate, _VisionClient())
    result = asyncio.run(run_vision_benchmark(service, route_id=candidate.route_id))
    assert result["status"] == "SUCCEEDED"
    assert result["vision_measured"] is True
    assert result["vision_success_rate"] == 1.0
    assert result["promotion_applied"] is False
    assert len(service._routing_repository.measurements) == 1
    assert service._routing_repository.availability == []


def test_rate_limit_blocks_suite_without_poisoning_vision_score() -> None:
    candidate = next(item for item in stage5_declared_candidates() if item.model == "qwen/qwen3.8-27b")
    service = _Service(candidate, _VisionClient(fail_after=3))
    result = asyncio.run(run_vision_benchmark(service, route_id=candidate.route_id))
    assert result["status"] == "BLOCKED"
    assert result["vision_measured"] is False
    assert result["vision_success_rate"] is None
    assert result["promotion_candidate"] is False
    assert result["scored_case_count"] == 2
    assert result["attempted_case_count"] == 3
    assert result["cases"][-1]["provider_failure"] == "RATE_LIMITED"
    assert result["cases"][-1]["provider_http_status"] == 429
    assert result["cases"][-1]["scored"] is False
    assert service._routing_repository.measurements == []


def test_malformed_provider_response_is_unscored_not_visual_zero() -> None:
    candidate = next(item for item in stage5_declared_candidates() if item.model == "qwen/qwen3.8-27b")
    service = _Service(
        candidate,
        _VisionClient(fail_after=2, failure_code="MALFORMED_PROVIDER_RESPONSE"),
    )
    result = asyncio.run(run_vision_benchmark(service, route_id=candidate.route_id))
    assert result["status"] == "BLOCKED"
    assert result["scored_case_count"] == 1
    assert result["attempted_case_count"] == 2
    assert result["cases"][-1]["provider_failure"] == "MALFORMED_PROVIDER_RESPONSE"
    assert result["cases"][-1]["scored"] is False
    assert service._routing_repository.measurements == []


def test_disabled_visual_route_cannot_execute_even_with_stale_client() -> None:
    candidate = next(item for item in stage5_declared_candidates() if item.model == "qwen/qwen3.8-27b")
    service = _Service(candidate, _VisionClient(), enabled=False)
    with pytest.raises(ModelIntelligenceError) as exc_info:
        asyncio.run(run_vision_benchmark(service, route_id=candidate.route_id))
    assert exc_info.value.code == "VISION_ROUTE_DISABLED"
    assert service._VisionClient.calls if False else True
