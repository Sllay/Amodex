"""Owner-only, opt-in NVIDIA upstream diagnostics.

This path never updates model measurements, lifecycle, provider availability,
chat history or benchmark state. It makes one fixed synthetic request per click.
Do not add arbitrary URLs, prompts, models or uploaded image inputs here.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from uuid import uuid4

import httpx

from amodex.model_intelligence.vision_benchmarks import vision_benchmark_cases
from amodex.providers.capabilities import BillingClass
from amodex.providers.catalog import stage5_declared_candidates
from amodex.providers.nvidia import NvidiaProvider
from amodex.providers.openai_compatible import (
    _classify_status, _extract_content_text, _optional_int,
    _retry_after, _safe_error_message,
)
from amodex.providers.registry import ProviderExecutionDenied

MODEL = "mistralai/mistral-large-4-1t-a52b"
ROUTE_ID = "nvidia:mistralai/mistral-large-4-1t-a52b:nvidia-free-primary"
URL = "https://integrate.api.nvidia.com/v1/chat/completions"
TEXT_SENTINEL = "AMODEX_DIAG_TEXT_OK"
VISION_SENTINEL = "AMODEX 4827"
_LOG = logging.getLogger("amodex.diagnostics.nvidia")
_COOLDOWN = 40.0
_REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")


def _safe_excerpt(value: object, secret: str, limit: int = 250) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = value.replace(secret, "[REDACTED]") if secret else value
    cleaned = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", cleaned)
    cleaned = re.sub(r"nvapi-[a-zA-Z0-9_-]+", "[REDACTED]", cleaned)
    return cleaned.replace("\\r", " ").replace("\\n", " ")[:limit]


def _response_choice(body: object) -> tuple[str | None, str | None, bool, str | None]:
    if not isinstance(body, dict):
        return None, None, False, "expected JSON object"
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, None, False, "missing choices[0]"
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        return None, None, False, "missing message object"
    finish = choice.get("finish_reason")
    reasoning = message.get("reasoning_content")
    content = message.get("content")
    if content is None or content == "":
        return None, str(finish) if finish is not None else None, bool(reasoning), None
    try:
        text = _extract_content_text(content)
    except (TypeError, ValueError):
        return None, str(finish) if finish is not None else None, bool(reasoning), "unsupported content type"
    return text, str(finish) if finish is not None else None, bool(reasoning), None


class NvidiaUpstreamDiagnostics:
    """One optional diagnostic per click; fixed NVIDIA route and free-only gate."""

    def __init__(self, service: object) -> None:
        self.service = service
        self._lock = threading.Lock()
        self._busy = False
        self._last_run: dict[str, float] = {}

    def configuration(self) -> dict[str, object]:
        key = os.environ.get("NVIDIA_API_KEY", "")
        configured = os.environ.get("AMODEX_NVIDIA_BILLING_CLASS", "unknown").strip().lower()
        row = self.service._repository.get_route(ROUTE_ID)
        declared = next(
            (item for item in stage5_declared_candidates() if item.route_id == ROUTE_ID),
            None,
        )
        pool = self.service._execution_registry._pools.get("nvidia-free-primary")
        ready = bool(
            row and row.get("enabled") is True and declared
            and declared.cost_class == BillingClass.FREE
            and pool and pool.enabled and pool.provider == "nvidia"
            and pool.billing_class == BillingClass.FREE
            and key and not key.lower().startswith("bearer ")
        )
        return {
            "endpoint": URL,
            "provider": "nvidia",
            "model": MODEL,
            "route_id": ROUTE_ID,
            "active_route": bool(row and row.get("enabled") is True),
            "pool_enabled": bool(pool and pool.enabled),
            "declared_billing": configured,
            "pool_billing": pool.billing_class.value if pool else "missing",
            "key_present": bool(key),
            "key_looks_like_nvidia": key.startswith("nvapi-") if key else None,
            "key_has_bearer_prefix": key.lower().startswith("bearer ") if key else False,
            "key_has_outer_whitespace": key != key.strip() if key else False,
            "admission_ready": ready,
            "note": "Free is the configured policy, not proof of upstream account quota.",
        }

    def _admit(self) -> NvidiaProvider:
        row = self.service._repository.get_route(ROUTE_ID)
        if not row or row.get("enabled") is not True:
            raise ProviderExecutionDenied("route missing or disabled")
        candidate = next(
            (c for c in stage5_declared_candidates() if c.route_id == ROUTE_ID), None
        )
        if candidate is None or candidate.cost_class != BillingClass.FREE:
            raise ProviderExecutionDenied("route not declared free")
        client = self.service._execution_registry.client_for(candidate)
        if not isinstance(client, NvidiaProvider):
            raise ProviderExecutionDenied("NVIDIA adapter not configured")
        if client._api_key.lower().startswith("bearer "):
            raise ProviderExecutionDenied("key includes Bearer prefix; use raw key in NVIDIA_API_KEY")
        return client

    def run(self, *, mode: str) -> dict[str, object]:
        if mode not in {"text", "vision"}:
            raise ValueError("mode must be text or vision")
        diagnostic_id = str(uuid4())
        now = time.monotonic()
        with self._lock:
            if self._busy:
                return {"diagnostic_id": diagnostic_id, "mode": mode, "outcome": "BUSY",
                        "provider_call_performed": False}
            elapsed = now - self._last_run.get(mode, -1e9)
            if elapsed < _COOLDOWN:
                return {"diagnostic_id": diagnostic_id, "mode": mode, "outcome": "COOLDOWN",
                        "retry_after_seconds": round(_COOLDOWN - elapsed, 1),
                        "provider_call_performed": False}
            self._busy = True

        try:
            try:
                client = self._admit()
            except ProviderExecutionDenied as exc:
                return {
                    "diagnostic_id": diagnostic_id, "mode": mode,
                    "outcome": "LOCAL_ADMISSION_DENIED",
                    "reason": str(exc), "provider_call_performed": False,
                    "configuration": self.configuration(),
                }
            with self._lock:
                self._last_run[mode] = time.monotonic()
            result = self._probe(mode=mode, secret=client._api_key, diagnostic_id=diagnostic_id)
            return result
        finally:
            with self._lock:
                self._busy = False

    def _probe(self, *, mode: str, secret: str, diagnostic_id: str) -> dict[str, object]:
        import base64
        content: object
        image_len = 0
        if mode == "vision":
            case = next(c for c in vision_benchmark_cases() if c.case_id == "vision-ocr-v1")
            image_len = len(case.image_png)
            data_url = "data:image/png;base64," + base64.b64encode(case.image_png).decode("ascii")
            content = [
                {"type": "text", "text": "Read the text displayed in the image and return only that text."},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]
        else:
            content = "Reply with exactly AMODEX_DIAG_TEXT_OK and no extra words."

        # Use the empirically accepted NVIDIA hosted profile for both text and vision.
        # An actual text-only request with reasoning_effort=none returned HTTP 400.
        # Fixed request: no response_format, model selection,
        # user prompt, upload, or arbitrary endpoint can enter this path.
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": content}],
            "stream": False,
            "max_tokens": 4096,
            "reasoning_effort": "high",
            "temperature": 0.5,
            "top_p": 1,
            "frequency_penalty": 0,
            "presence_penalty": 0,
        }
        result: dict[str, object] = {
            "diagnostic_id": diagnostic_id, "mode": mode,
            "provider": "nvidia", "model": MODEL,
            "endpoint": URL, "provider_call_performed": True,
            "request": {
                "stream": False, "reasoning_effort": payload["reasoning_effort"],
                "max_tokens": payload["max_tokens"], "has_image": mode == "vision",
                "image_mime": "image/png" if mode == "vision" else None,
                "image_bytes": image_len,
                "response_format_sent": False,
            },
        }
        started = time.perf_counter()
        try:
            response = httpx.post(
                URL, headers={
                    "Authorization": f"Bearer {secret}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                }, json=payload, timeout=65.0,
            )
        except httpx.TimeoutException:
            result.update(outcome="TIMEOUT", upstream_status=None)
            return self._finish(result, started)
        except httpx.HTTPError:
            result.update(outcome="NETWORK_ERROR", upstream_status=None)
            return self._finish(result, started)

        result["upstream_status"] = response.status_code
        request_id = response.headers.get("x-request-id", "")
        if _REQUEST_ID_RE.fullmatch(request_id):
            result["upstream_request_id"] = request_id
        result["retry_after_seconds"] = _retry_after(response)
        if response.status_code >= 400:
            message = _safe_error_message(response)
            result.update(
                outcome=_classify_status(response.status_code, message),
                upstream_error_excerpt=_safe_excerpt(message, secret),
            )
            return self._finish(result, started)
        try:
            body = response.json()
        except ValueError:
            result.update(outcome="NON_JSON_HTTP_200")
            return self._finish(result, started)
        text, finish, reasoning, shape_error = _response_choice(body)
        result["finish_reason"] = finish
        result["reasoning_present"] = reasoning
        result["content_present"] = bool(text)
        result["content_chars"] = len(text) if text else 0
        if isinstance(body, dict) and isinstance(body.get("usage"), dict):
            usage = body["usage"]
            result["usage"] = {
                "prompt_tokens": _optional_int(usage.get("prompt_tokens")),
                "completion_tokens": _optional_int(usage.get("completion_tokens")),
                "total_tokens": _optional_int(usage.get("total_tokens")),
            }
        if shape_error:
            result.update(outcome="RESPONSE_SCHEMA_MISMATCH", schema_issue=shape_error)
        elif not text and finish == "length":
            result.update(outcome="OUTPUT_TRUNCATED_IN_REASONING")
        elif not text:
            result.update(outcome="EMPTY_FINAL_ANSWER")
        else:
            expected = VISION_SENTINEL if mode == "vision" else TEXT_SENTINEL
            result["response_excerpt"] = _safe_excerpt(text, secret)
            result["expected"] = expected
            result["exact_match"] = text.strip().upper() == expected
            result["outcome"] = "PASS" if result["exact_match"] else "ANSWER_MISMATCH"
        return self._finish(result, started)

    @staticmethod
    def _finish(result: dict[str, object], started: float) -> dict[str, object]:
        result["latency_ms"] = round((time.perf_counter() - started) * 1000)
        _LOG.info(
            "nvidia_upstream_diagnostic id=%s mode=%s upstream_status=%s outcome=%s finish_reason=%s",
            result.get("diagnostic_id"), result.get("mode"), result.get("upstream_status"),
            result.get("outcome"), result.get("finish_reason"),
        )
        return result
