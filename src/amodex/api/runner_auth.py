"""One runner credential authority, shared by the machine and private gates.

The shared bearer is a control credential, not proof of a GitHub run's identity.
Only these four POST routes may pass the private gate without a human session.
"""
from __future__ import annotations

import hmac
import re
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

_JOB_ID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_MACHINE_PATH = re.compile(rf"/v1/runner-jobs/{_JOB_ID}/(?:claim|running|heartbeat|report)")


def is_runner_machine_route(request: Request) -> bool:
    return request.method == "POST" and _MACHINE_PATH.fullmatch(request.url.path) is not None


@dataclass(frozen=True, slots=True)
class RunnerAuthorization:
    token: str | None = field(default=None, repr=False)

    def require(self, request: Request) -> None:
        if not self.token or not self.token.strip():
            raise HTTPException(status_code=503, detail={
                "code": "RUNNER_AUTH_NOT_CONFIGURED",
                "message": "AMODEX_RUNNER_CONTROL_TOKEN is not configured",
            })
        supplied = request.headers.getlist("authorization")
        if len(supplied) != 1 or not hmac.compare_digest(
            supplied[0].encode("utf-8"), f"Bearer {self.token}".encode("utf-8")
        ):
            raise HTTPException(status_code=401, detail="invalid runner authorization")


def runner_authorization(app: FastAPI) -> RunnerAuthorization:
    # A custom base app without a configured runner authority fails closed.
    return getattr(app.state, "runner_authorization", RunnerAuthorization())


def rejection(exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail},
                        headers={"Cache-Control": "no-store"})


def install_runner_authorization(app: FastAPI, token: str | None) -> RunnerAuthorization:
    authority = RunnerAuthorization(token)
    app.state.runner_authorization = authority

    @app.middleware("http")
    async def machine_boundary(request: Request, call_next):
        # Authenticate before body/query validation, including malformed JSON.
        # The handler dependency also protects router matches outside this exact
        # machine allowlist (which never bypass the human private gate).
        if is_runner_machine_route(request):
            try:
                authority.require(request)
            except HTTPException as exc:
                return rejection(exc)
        return await call_next(request)

    return authority
