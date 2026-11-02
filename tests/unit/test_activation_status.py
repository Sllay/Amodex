from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from amodex.api.activation_status import (
    build_activation_router,
    release_identity,
    zero_cost_readiness,
)


def test_release_identity_prefers_render_commit(monkeypatch) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123")
    monkeypatch.setenv("AMODEX_RELEASE_SHA", "fallback")
    monkeypatch.setenv("RENDER_SERVICE_ID", "srv-1")
    assert release_identity(app_version="1.2.3") == {
        "version": "1.2.3",
        "git_sha": "abc123",
        "host": "render",
    }


def test_release_identity_uses_explicit_fallback(monkeypatch) -> None:
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.delenv("RENDER_SERVICE_ID", raising=False)
    monkeypatch.setenv("AMODEX_RELEASE_SHA", "deadbeef")
    assert release_identity(app_version="dev") == {
        "version": "dev",
        "git_sha": "deadbeef",
        "host": "generic",
    }


def test_zero_cost_blocks_configured_unknown_provider(monkeypatch) -> None:
    for key in (
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "NVIDIA_API_KEY",
        "AMODEX_STT_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "secret-for-test")
    monkeypatch.delenv("AMODEX_GROQ_BILLING_CLASS", raising=False)
    snapshot = zero_cost_readiness()
    assert snapshot["ready"] is False
    assert "groq-free-primary" in snapshot["violations"]


def test_zero_cost_accepts_explicit_free_provider(monkeypatch) -> None:
    for key in (
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "NVIDIA_API_KEY",
        "AMODEX_STT_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "secret-for-test")
    monkeypatch.setenv("AMODEX_GROQ_BILLING_CLASS", "free")
    snapshot = zero_cost_readiness()
    assert snapshot["ready"] is True
    assert snapshot["configured_provider_pools"] == 1
    assert snapshot["configured_free_provider_pools"] == 1


def test_enabled_stt_requires_free_billing(monkeypatch) -> None:
    for key in (
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "NVIDIA_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AMODEX_STT_ENABLED", "true")
    monkeypatch.setenv("AMODEX_STT_BILLING_CLASS", "unknown")
    snapshot = zero_cost_readiness()
    assert snapshot["ready"] is False
    assert "stt" in snapshot["violations"]


def test_readiness_route_is_public_shape_and_activation_status_is_richer(monkeypatch) -> None:
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.delenv("AMODEX_RELEASE_SHA", raising=False)
    app = FastAPI()
    app.include_router(build_activation_router(dsn="", app_version="test"))
    client = TestClient(app)

    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    ready = client.get("/readyz")
    assert ready.status_code == 503
    assert ready.json()["ready"] is False
    assert "expected_migrations" not in ready.json()["database"]

    activation = client.get("/v1/activation/status")
    assert activation.status_code == 200
    assert activation.json()["ready"] is False
    assert "expected_migrations" in activation.json()["database"]


def test_access_gate_keeps_readiness_public_and_activation_status_private() -> None:
    source = Path("src/amodex/api/access_gate.py").read_text(encoding="utf-8")
    public_section = source.split("_PUBLIC_PATHS", 1)[1].split(")", 1)[0]
    assert '"/healthz"' in public_section
    assert '"/readyz"' in public_section
    assert '"/v1/activation/status"' not in public_section


def test_activation_route_does_not_shadow_legacy_system_status() -> None:
    activation = Path("src/amodex/api/activation_status.py").read_text(encoding="utf-8")
    control_plane = Path("src/amodex/api/app.py").read_text(encoding="utf-8")
    assert '@router.get("/v1/activation/status")' in activation
    assert '@router.get("/v1/system/status")' not in activation
    assert '@app.get("/v1/system/status")' in control_plane


def test_workspace_release_is_activation_v1() -> None:
    source = Path("src/amodex/api/workspace_app.py").read_text(encoding="utf-8")
    assert '_RELEASE = "0.7.6-activation-v1"' in source
    assert "build_activation_router" in source
