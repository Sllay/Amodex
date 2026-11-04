from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from amodex.api.access_gate import AccessGateConfig, install_access_gate


def _protected_app(*, passcode: str = "123456", secret: str = "s" * 48) -> FastAPI:
    app = FastAPI()

    @app.get("/")
    async def root() -> dict[str, bool]:
        return {"private": True}

    @app.get("/api/private")
    async def private_api() -> dict[str, bool]:
        return {"private": True}

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    enabled = install_access_gate(
        app,
        AccessGateConfig(passcode=passcode, session_secret=secret, session_ttl_seconds=3600),
    )
    assert enabled is True
    return app


def test_gate_blocks_web_and_api_but_keeps_health_public() -> None:
    client = TestClient(_protected_app(), base_url="https://amodex.test")

    web = client.get("/", headers={"accept": "text/html"})
    assert web.status_code == 401
    assert "Passcode de 6 dígitos" in web.text
    assert "Acesso verificado no servidor" in web.text

    api = client.get("/api/private")
    assert api.status_code == 401
    assert api.json() == {"detail": "Amodex access passcode required"}

    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}


def test_correct_passcode_issues_secure_http_only_session() -> None:
    client = TestClient(_protected_app(), base_url="https://amodex.test")

    wrong = client.post("/auth/passcode", data={"passcode": "654321"}, follow_redirects=False)
    assert wrong.status_code == 401
    assert "Código inválido" in wrong.text

    login = client.post("/auth/passcode", data={"passcode": "123456"}, follow_redirects=False)
    assert login.status_code == 303
    assert login.headers["location"] == "/"
    set_cookie = login.headers["set-cookie"]
    assert "__Host-amodex_access=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=lax" in set_cookie

    root = client.get("/")
    assert root.status_code == 200
    assert root.json() == {"private": True}

    api = client.get("/api/private")
    assert api.status_code == 200
    assert api.json() == {"private": True}


def test_tampered_cookie_cannot_bypass_gate() -> None:
    client = TestClient(_protected_app(), base_url="https://amodex.test")
    login = client.post("/auth/passcode", data={"passcode": "123456"}, follow_redirects=False)
    assert login.status_code == 303

    token = client.cookies.get("__Host-amodex_access")
    assert token
    client.cookies.set("__Host-amodex_access", token[:-1] + ("A" if token[-1] != "A" else "B"))

    blocked = client.get("/api/private")
    assert blocked.status_code == 401


def test_changing_passcode_revokes_old_sessions_even_with_same_secret() -> None:
    secret = "k" * 48
    first = TestClient(_protected_app(passcode="123456", secret=secret), base_url="https://amodex.test")
    login = first.post("/auth/passcode", data={"passcode": "123456"}, follow_redirects=False)
    assert login.status_code == 303
    token = first.cookies.get("__Host-amodex_access")
    assert token

    second = TestClient(_protected_app(passcode="222222", secret=secret), base_url="https://amodex.test")
    second.cookies.set("__Host-amodex_access", token)
    assert second.get("/api/private").status_code == 401


def test_repeated_failures_are_rate_limited() -> None:
    client = TestClient(_protected_app(), base_url="https://amodex.test")
    for _ in range(5):
        response = client.post("/auth/passcode", data={"passcode": "000000"}, follow_redirects=False)
        assert response.status_code == 401

    blocked = client.post("/auth/passcode", data={"passcode": "000000"}, follow_redirects=False)
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


def test_environment_contract_is_fail_closed_when_passcode_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMODEX_ACCESS_PASSCODE", "123456")
    monkeypatch.delenv("AMODEX_ACCESS_SESSION_SECRET", raising=False)
    with pytest.raises(ValueError, match="at least 32 characters"):
        AccessGateConfig.from_environment()

    monkeypatch.setenv("AMODEX_ACCESS_PASSCODE", "12345x")
    monkeypatch.setenv("AMODEX_ACCESS_SESSION_SECRET", "x" * 48)
    with pytest.raises(ValueError, match="exactly 6 digits"):
        AccessGateConfig.from_environment()


def test_no_passcode_keeps_local_and_test_apps_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AMODEX_ACCESS_PASSCODE", raising=False)
    monkeypatch.delenv("AMODEX_ACCESS_SESSION_SECRET", raising=False)
    app = FastAPI()

    @app.get("/")
    async def root() -> dict[str, bool]:
        return {"open": True}

    assert install_access_gate(app) is False
    client = TestClient(app)
    assert client.get("/").json() == {"open": True}
