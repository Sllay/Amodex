"""Server-side access gate for private Amodex deployments.

The gate is intentionally enforced before application routes, so hiding the web
workspace cannot be bypassed by calling API endpoints directly.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

_COOKIE_NAME = "__Host-amodex_access"
_PUBLIC_PATHS = frozenset({"/healthz", "/auth/passcode", "/auth/logout"})
_PASSCODE_RE = re.compile(r"^[0-9]{6}$")
_DEFAULT_TTL_SECONDS = 12 * 60 * 60


@dataclass(frozen=True, slots=True)
class AccessGateConfig:
    passcode: str
    session_secret: str
    session_ttl_seconds: int = _DEFAULT_TTL_SECONDS

    @classmethod
    def from_environment(cls) -> "AccessGateConfig | None":
        passcode = os.environ.get("AMODEX_ACCESS_PASSCODE", "").strip()
        if not passcode:
            return None
        if not _PASSCODE_RE.fullmatch(passcode):
            raise ValueError("AMODEX_ACCESS_PASSCODE must contain exactly 6 digits")

        session_secret = os.environ.get("AMODEX_ACCESS_SESSION_SECRET", "").strip()
        if len(session_secret) < 32:
            raise ValueError("AMODEX_ACCESS_SESSION_SECRET must contain at least 32 characters")

        raw_ttl = os.environ.get("AMODEX_ACCESS_SESSION_TTL_SECONDS", str(_DEFAULT_TTL_SECONDS)).strip()
        try:
            ttl = int(raw_ttl)
        except ValueError as exc:
            raise ValueError("AMODEX_ACCESS_SESSION_TTL_SECONDS must be an integer") from exc
        if ttl < 300 or ttl > 7 * 24 * 60 * 60:
            raise ValueError("AMODEX_ACCESS_SESSION_TTL_SECONDS must be between 300 and 604800")
        return cls(passcode=passcode, session_secret=session_secret, session_ttl_seconds=ttl)


class _AttemptLimiter:
    """Small in-process limiter for a single-instance private deployment."""

    def __init__(self, *, max_failures: int = 5, window_seconds: int = 600, block_seconds: int = 900) -> None:
        self._max_failures = max_failures
        self._window_seconds = window_seconds
        self._block_seconds = block_seconds
        self._failures: dict[str, deque[float]] = {}
        self._blocked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def blocked_for(self, key: str, *, now: float | None = None) -> int:
        current = time.monotonic() if now is None else now
        with self._lock:
            until = self._blocked_until.get(key, 0.0)
            if until <= current:
                self._blocked_until.pop(key, None)
                return 0
            return max(1, int(until - current))

    def fail(self, key: str, *, now: float | None = None) -> int:
        current = time.monotonic() if now is None else now
        with self._lock:
            failures = self._failures.setdefault(key, deque())
            cutoff = current - self._window_seconds
            while failures and failures[0] < cutoff:
                failures.popleft()
            failures.append(current)
            if len(failures) >= self._max_failures:
                until = current + self._block_seconds
                self._blocked_until[key] = until
                failures.clear()
                return self._block_seconds
            return 0

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._blocked_until.pop(key, None)


def install_access_gate(app: FastAPI, config: AccessGateConfig | None = None) -> bool:
    """Install the private access gate when configured.

    Returns ``True`` when protection is active. With no configured passcode the
    application remains unchanged, which keeps local development and existing
    tests backward-compatible.
    """

    config = config or AccessGateConfig.from_environment()
    if config is None:
        return False

    limiter = _AttemptLimiter()
    signing_key = hmac.new(
        config.session_secret.encode("utf-8"),
        config.passcode.encode("ascii"),
        hashlib.sha256,
    ).digest()

    def issue_session() -> str:
        issued = int(time.time())
        expires = issued + config.session_ttl_seconds
        nonce = secrets.token_urlsafe(18)
        payload = f"v1.{issued}.{expires}.{nonce}"
        signature = _sign(payload, signing_key)
        return f"{payload}.{signature}"

    def session_is_valid(token: str | None) -> bool:
        if not token:
            return False
        pieces = token.split(".")
        if len(pieces) != 5 or pieces[0] != "v1":
            return False
        _, issued_raw, expires_raw, nonce, signature = pieces
        if not nonce or len(nonce) > 128:
            return False
        try:
            issued = int(issued_raw)
            expires = int(expires_raw)
        except ValueError:
            return False
        now = int(time.time())
        if issued > now + 60 or expires <= now or expires - issued != config.session_ttl_seconds:
            return False
        payload = ".".join(pieces[:4])
        return hmac.compare_digest(signature, _sign(payload, signing_key))

    @app.get("/auth/passcode", response_class=HTMLResponse, include_in_schema=False)
    async def access_login_page(request: Request) -> Response:
        if session_is_valid(request.cookies.get(_COOKIE_NAME)):
            return RedirectResponse(url="/", status_code=303)
        return HTMLResponse(_login_html())

    @app.post("/auth/passcode", include_in_schema=False)
    async def access_login_submit(request: Request) -> Response:
        client_key = _client_key(request)
        retry_after = limiter.blocked_for(client_key)
        if retry_after:
            return HTMLResponse(
                _login_html(error="Muitas tentativas. Tente novamente em alguns minutos."),
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        body = await request.body()
        if len(body) > 1024:
            candidate = ""
        else:
            try:
                values = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            except UnicodeDecodeError:
                values = {}
            candidate = values.get("passcode", [""])[0].strip()

        valid_shape = bool(_PASSCODE_RE.fullmatch(candidate))
        valid_code = valid_shape and hmac.compare_digest(candidate, config.passcode)
        if not valid_code:
            limiter.fail(client_key)
            return HTMLResponse(_login_html(error="Código inválido."), status_code=401)

        limiter.clear(client_key)
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            key=_COOKIE_NAME,
            value=issue_session(),
            max_age=config.session_ttl_seconds,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/auth/logout", include_in_schema=False)
    async def access_logout() -> Response:
        response = RedirectResponse(url="/auth/passcode", status_code=303)
        response.delete_cookie(key=_COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.middleware("http")
    async def private_access_boundary(request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC_PATHS:
            return await call_next(request)

        if session_is_valid(request.cookies.get(_COOKIE_NAME)):
            return await call_next(request)

        accept = request.headers.get("accept", "")
        if request.method in {"GET", "HEAD"} and "text/html" in accept.lower():
            return HTMLResponse(_login_html(), status_code=401, headers={"Cache-Control": "no-store"})
        return JSONResponse(
            {"detail": "Amodex access passcode required"},
            status_code=401,
            headers={"Cache-Control": "no-store"},
        )

    return True


def _sign(payload: str, signing_key: bytes) -> str:
    digest = hmac.new(signing_key, payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    forwarded_ip = forwarded.split(",")[-1].strip() if forwarded else ""
    peer_ip = request.client.host if request.client else "unknown"
    material = f"{peer_ip}|{forwarded_ip}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _login_html(*, error: str = "") -> str:
    error_html = f'<p class="error" role="alert">{error}</p>' if error else ""
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Amodex — Acesso privado</title>
<style>
:root{{color-scheme:dark;background:#090b10;color:#f3f5f7;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100dvh;display:grid;place-items:center;background:radial-gradient(circle at 50% 20%,#171c27 0,#090b10 48%,#050608 100%);padding:24px}}
.card{{width:min(100%,390px);padding:28px;border:1px solid #252b38;border-radius:20px;background:rgba(13,16,23,.94);box-shadow:0 24px 80px rgba(0,0,0,.45)}}
.badge{{display:inline-block;font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:#9da7b8;margin-bottom:12px}}h1{{font-size:28px;margin:0 0 8px}}p{{color:#aab3c2;line-height:1.55;margin:0 0 20px}}
label{{display:block;font-size:13px;color:#c5cbd6;margin-bottom:8px}}input{{width:100%;height:54px;border:1px solid #30394a;border-radius:13px;background:#090c12;color:white;font-size:24px;letter-spacing:.32em;text-align:center;padding:0 12px;outline:none}}input:focus{{border-color:#6f7d97;box-shadow:0 0 0 3px rgba(111,125,151,.17)}}
button{{width:100%;height:50px;margin-top:14px;border:0;border-radius:13px;background:#f3f5f7;color:#090b10;font-weight:750;font-size:15px;cursor:pointer}}.error{{margin:14px 0 0;color:#ff8f8f;font-size:13px}}.foot{{margin-top:18px;font-size:12px;color:#687386;text-align:center}}
</style>
</head>
<body>
<main class="card">
<span class="badge">Private workspace</span>
<h1>Amodex</h1>
<p>Este ambiente é privado. Digite o código de acesso para continuar.</p>
<form method="post" action="/auth/passcode" autocomplete="off">
<label for="passcode">Passcode de 6 dígitos</label>
<input id="passcode" name="passcode" inputmode="numeric" pattern="[0-9]{{6}}" minlength="6" maxlength="6" autocomplete="one-time-code" required autofocus>
<button type="submit">Entrar</button>
</form>
{error_html}
<div class="foot">Acesso verificado no servidor.</div>
</main>
</body>
</html>"""
