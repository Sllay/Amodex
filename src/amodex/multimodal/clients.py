"""Provider clients for explicit Step 3 media/search actions.

No automatic route selection happens here. Every model-backed capability must be
explicitly configured and declared FREE. This is intentionally separate from the
Stage 5/Step 4 quality-routing lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import os
from typing import Protocol

import httpx

from amodex.multimodal.models import ResearchSource


class MultimodalClientError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class TextCapabilityResult:
    text: str
    provider: str
    model: str | None
    billing_class: str | None
    provenance: dict[str, object]


@dataclass(frozen=True, slots=True)
class AudioCapabilityResult:
    data: bytes
    media_type: str
    extension: str
    provider: str
    model: str | None
    billing_class: str | None
    provenance: dict[str, object]


@dataclass(frozen=True, slots=True)
class ResearchCapabilityResult:
    sources: tuple[ResearchSource, ...]
    provider: str
    provenance: dict[str, object]


class VisionClient(Protocol):
    @property
    def ready(self) -> bool: ...
    @property
    def readiness_reason(self) -> str: ...
    @property
    def provider(self) -> str: ...
    @property
    def model(self) -> str | None: ...
    @property
    def billing_class(self) -> str | None: ...
    async def analyze(self, *, data: bytes, media_type: str, prompt: str) -> TextCapabilityResult: ...


class SttClient(Protocol):
    @property
    def ready(self) -> bool: ...
    @property
    def readiness_reason(self) -> str: ...
    @property
    def provider(self) -> str: ...
    @property
    def model(self) -> str | None: ...
    @property
    def billing_class(self) -> str | None: ...
    async def transcribe(self, *, data: bytes, name: str, media_type: str, language: str | None) -> TextCapabilityResult: ...


class TtsClient(Protocol):
    @property
    def ready(self) -> bool: ...
    @property
    def readiness_reason(self) -> str: ...
    @property
    def provider(self) -> str: ...
    @property
    def model(self) -> str | None: ...
    @property
    def billing_class(self) -> str | None: ...
    async def synthesize(self, *, text: str, voice: str | None) -> AudioCapabilityResult: ...


class ResearchClient(Protocol):
    @property
    def ready(self) -> bool: ...
    @property
    def readiness_reason(self) -> str: ...
    @property
    def provider(self) -> str: ...
    async def search(self, *, query: str, max_results: int, language: str | None) -> ResearchCapabilityResult: ...


class DisabledVisionClient:
    provider = "disabled"
    model = None
    billing_class = None

    def __init__(self, reason: str = "VISION_NOT_CONFIGURED") -> None:
        self.readiness_reason = reason

    @property
    def ready(self) -> bool:
        return False

    async def analyze(self, **_: object) -> TextCapabilityResult:
        raise MultimodalClientError(self.readiness_reason, "vision capability is not configured")


class DisabledSttClient:
    provider = "disabled"
    model = None
    billing_class = None

    def __init__(self, reason: str = "STT_NOT_CONFIGURED") -> None:
        self.readiness_reason = reason

    @property
    def ready(self) -> bool:
        return False

    async def transcribe(self, **_: object) -> TextCapabilityResult:
        raise MultimodalClientError(self.readiness_reason, "speech-to-text capability is not configured")


class DisabledTtsClient:
    provider = "disabled"
    model = None
    billing_class = None

    def __init__(self, reason: str = "SERVER_TTS_NOT_CONFIGURED") -> None:
        self.readiness_reason = reason

    @property
    def ready(self) -> bool:
        return False

    async def synthesize(self, **_: object) -> AudioCapabilityResult:
        raise MultimodalClientError(self.readiness_reason, "server text-to-speech is not configured")


class DisabledResearchClient:
    provider = "disabled"

    def __init__(self, reason: str = "WEB_RESEARCH_NOT_CONFIGURED") -> None:
        self.readiness_reason = reason

    @property
    def ready(self) -> bool:
        return False

    async def search(self, **_: object) -> ResearchCapabilityResult:
        raise MultimodalClientError(self.readiness_reason, "web research backend is not configured")


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_env_ref(ref: str) -> str:
    if not ref.startswith("env://"):
        raise MultimodalClientError("INVALID_SECRET_REFERENCE", "provider secret must use env:// reference")
    name = ref.removeprefix("env://")
    if not name or not name.replace("_", "A").isalnum() or name.upper() != name:
        raise MultimodalClientError("INVALID_SECRET_REFERENCE", "provider secret reference must name an uppercase environment variable")
    value = os.environ.get(name, "")
    if not value:
        raise MultimodalClientError("PROVIDER_SECRET_MISSING", f"environment secret {name} is missing")
    return value


def _provider_defaults(provider: str) -> tuple[str | None, str | None, str | None]:
    provider = provider.lower()
    if provider == "openrouter":
        return "https://openrouter.ai/api/v1", "env://OPENROUTER_API_KEY", os.environ.get("AMODEX_OPENROUTER_BILLING_CLASS")
    if provider == "groq":
        return "https://api.groq.com/openai/v1", "env://GROQ_API_KEY", os.environ.get("AMODEX_GROQ_BILLING_CLASS")
    if provider == "nvidia":
        return "https://integrate.api.nvidia.com/v1", "env://NVIDIA_API_KEY", os.environ.get("AMODEX_NVIDIA_BILLING_CLASS")
    return None, None, None


class OpenAICompatibleVisionClient:
    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        model: str,
        api_key_ref: str,
        billing_class: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 45.0,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_ref = api_key_ref
        self.billing_class = billing_class.lower()
        self.transport = transport
        self.timeout = timeout
        self.readiness_reason = "READY" if self.billing_class == "free" else "VISION_BILLING_NOT_FREE"

    @property
    def ready(self) -> bool:
        return self.billing_class == "free"

    async def analyze(self, *, data: bytes, media_type: str, prompt: str) -> TextCapabilityResult:
        if not self.ready:
            raise MultimodalClientError("VISION_BILLING_NOT_FREE", "vision is blocked because billing is not explicitly FREE")
        key = _resolve_env_ref(self.api_key_ref)
        encoded = base64.b64encode(data).decode("ascii")
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{encoded}"}},
                    ],
                }
            ],
        }
        async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
                json=payload,
            )
        if response.status_code >= 400:
            raise MultimodalClientError("VISION_PROVIDER_ERROR", f"vision provider returned HTTP {response.status_code}")
        body = response.json()
        content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
        if isinstance(content, list):
            content = "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("text"))
        if not isinstance(content, str) or not content.strip():
            raise MultimodalClientError("VISION_EMPTY_RESPONSE", "vision provider returned no text")
        return TextCapabilityResult(
            text=content.strip(), provider=self.provider, model=self.model, billing_class=self.billing_class,
            provenance={"endpoint": "chat/completions", "input_media_type": media_type},
        )


class GroqSttClient:
    provider = "groq"

    def __init__(
        self,
        *,
        model: str = "whisper-large-v3-turbo",
        api_key_ref: str = "env://GROQ_API_KEY",
        billing_class: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self.api_key_ref = api_key_ref
        self.billing_class = billing_class.lower()
        self.transport = transport
        self.timeout = timeout
        self.readiness_reason = "READY" if self.billing_class == "free" else "STT_BILLING_NOT_FREE"

    @property
    def ready(self) -> bool:
        return self.billing_class == "free"

    async def transcribe(self, *, data: bytes, name: str, media_type: str, language: str | None) -> TextCapabilityResult:
        if not self.ready:
            raise MultimodalClientError("STT_BILLING_NOT_FREE", "speech-to-text is blocked because billing is not explicitly FREE")
        key = _resolve_env_ref(self.api_key_ref)
        form = {"model": self.model, "response_format": "json"}
        if language:
            form["language"] = language
        async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"authorization": f"Bearer {key}"},
                data=form,
                files={"file": (name, data, media_type)},
            )
        if response.status_code >= 400:
            raise MultimodalClientError("STT_PROVIDER_ERROR", f"speech-to-text provider returned HTTP {response.status_code}")
        text = response.json().get("text")
        if not isinstance(text, str) or not text.strip():
            raise MultimodalClientError("STT_EMPTY_RESPONSE", "speech-to-text provider returned no transcript")
        return TextCapabilityResult(
            text=text.strip(), provider=self.provider, model=self.model, billing_class=self.billing_class,
            provenance={"endpoint": "audio/transcriptions", "language": language},
        )


class OpenAICompatibleTtsClient:
    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        model: str,
        voice: str,
        api_key_ref: str,
        billing_class: str,
        response_format: str = "mp3",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.voice = voice
        self.api_key_ref = api_key_ref
        self.billing_class = billing_class.lower()
        self.response_format = response_format
        self.transport = transport
        self.timeout = timeout
        self.readiness_reason = "READY" if self.billing_class == "free" else "TTS_BILLING_NOT_FREE"

    @property
    def ready(self) -> bool:
        return self.billing_class == "free"

    async def synthesize(self, *, text: str, voice: str | None) -> AudioCapabilityResult:
        if not self.ready:
            raise MultimodalClientError("TTS_BILLING_NOT_FREE", "server TTS is blocked because billing is not explicitly FREE")
        key = _resolve_env_ref(self.api_key_ref)
        chosen_voice = voice or self.voice
        payload = {
            "model": self.model,
            "input": text,
            "voice": chosen_voice,
            "response_format": self.response_format,
        }
        async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/audio/speech",
                headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
                json=payload,
            )
        if response.status_code >= 400:
            raise MultimodalClientError("TTS_PROVIDER_ERROR", f"server TTS provider returned HTTP {response.status_code}")
        media_by_format = {
            "mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
            "flac": "audio/flac", "mulaw": "audio/basic",
        }
        return AudioCapabilityResult(
            data=response.content,
            media_type=media_by_format.get(self.response_format, response.headers.get("content-type", "audio/mpeg").split(";")[0]),
            extension=self.response_format,
            provider=self.provider,
            model=self.model,
            billing_class=self.billing_class,
            provenance={"endpoint": "audio/speech", "voice": chosen_voice},
        )


class SearxngResearchClient:
    provider = "searxng"

    def __init__(
        self,
        *,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self.timeout = timeout
        self.readiness_reason = "READY" if self.base_url else "WEB_RESEARCH_NOT_CONFIGURED"

    @property
    def ready(self) -> bool:
        return bool(self.base_url)

    async def search(self, *, query: str, max_results: int, language: str | None) -> ResearchCapabilityResult:
        if not self.ready:
            raise MultimodalClientError("WEB_RESEARCH_NOT_CONFIGURED", "SearXNG is not configured")
        params: dict[str, object] = {"q": query, "format": "json", "safesearch": 1}
        if language:
            params["language"] = language
        async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout, follow_redirects=False) as client:
            response = await client.get(f"{self.base_url}/search", params=params)
        if response.status_code >= 400:
            raise MultimodalClientError("RESEARCH_PROVIDER_ERROR", f"SearXNG returned HTTP {response.status_code}")
        body = response.json()
        sources: list[ResearchSource] = []
        for item in body.get("results", []):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "Resultado sem título").strip()
            url = str(item.get("url") or "").strip()
            if not url.startswith(("https://", "http://")):
                continue
            engines = item.get("engines") or item.get("engine")
            if isinstance(engines, list):
                engine = ",".join(str(x) for x in engines[:4])
            elif engines is None:
                engine = None
            else:
                engine = str(engines)
            sources.append(
                ResearchSource(
                    title=title[:1000],
                    url=url[:4000],
                    snippet=str(item.get("content") or "")[:8000],
                    engine=engine[:160] if engine else None,
                )
            )
            if len(sources) >= max_results:
                break
        return ResearchCapabilityResult(
            sources=tuple(sources),
            provider=self.provider,
            provenance={"endpoint": "/search", "result_count": len(sources), "page_fetch": False},
        )


@dataclass(frozen=True, slots=True)
class MultimodalClients:
    vision: VisionClient
    stt: SttClient
    tts: TtsClient
    research: ResearchClient


def multimodal_clients_from_environment() -> MultimodalClients:
    vision: VisionClient = DisabledVisionClient()
    if _truthy("AMODEX_VISION_ENABLED"):
        provider = os.environ.get("AMODEX_VISION_PROVIDER", "").strip().lower()
        model = os.environ.get("AMODEX_VISION_MODEL", "").strip()
        default_base, default_ref, default_billing = _provider_defaults(provider)
        base_url = os.environ.get("AMODEX_VISION_BASE_URL", default_base or "").strip()
        key_ref = os.environ.get("AMODEX_VISION_API_KEY_REF", default_ref or "").strip()
        billing = os.environ.get("AMODEX_VISION_BILLING_CLASS", default_billing or "unknown").strip().lower()
        if provider and model and base_url and key_ref:
            vision = OpenAICompatibleVisionClient(
                provider=provider, base_url=base_url, model=model,
                api_key_ref=key_ref, billing_class=billing,
            )
        else:
            vision = DisabledVisionClient("VISION_CONFIGURATION_INCOMPLETE")

    stt: SttClient = DisabledSttClient()
    if _truthy("AMODEX_STT_ENABLED"):
        billing = os.environ.get("AMODEX_STT_BILLING_CLASS", os.environ.get("AMODEX_GROQ_BILLING_CLASS", "unknown")).strip().lower()
        stt = GroqSttClient(
            model=os.environ.get("AMODEX_STT_MODEL", "whisper-large-v3-turbo").strip(),
            api_key_ref=os.environ.get("AMODEX_STT_API_KEY_REF", "env://GROQ_API_KEY").strip(),
            billing_class=billing,
        )

    tts: TtsClient = DisabledTtsClient()
    if _truthy("AMODEX_TTS_ENABLED"):
        provider = os.environ.get("AMODEX_TTS_PROVIDER", "").strip().lower()
        default_base, default_ref, default_billing = _provider_defaults(provider)
        base_url = os.environ.get("AMODEX_TTS_BASE_URL", default_base or "").strip()
        key_ref = os.environ.get("AMODEX_TTS_API_KEY_REF", default_ref or "").strip()
        model = os.environ.get("AMODEX_TTS_MODEL", "").strip()
        voice = os.environ.get("AMODEX_TTS_VOICE", "").strip()
        billing = os.environ.get("AMODEX_TTS_BILLING_CLASS", default_billing or "unknown").strip().lower()
        if provider and base_url and key_ref and model and voice:
            tts = OpenAICompatibleTtsClient(
                provider=provider, base_url=base_url, model=model, voice=voice,
                api_key_ref=key_ref, billing_class=billing,
                response_format=os.environ.get("AMODEX_TTS_RESPONSE_FORMAT", "mp3").strip().lower(),
            )
        else:
            tts = DisabledTtsClient("TTS_CONFIGURATION_INCOMPLETE")

    searxng_url = os.environ.get("AMODEX_RESEARCH_SEARXNG_URL", "").strip()
    research: ResearchClient = SearxngResearchClient(base_url=searxng_url) if searxng_url else DisabledResearchClient()

    return MultimodalClients(vision=vision, stt=stt, tts=tts, research=research)
