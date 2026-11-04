"""Private conditional-response helpers for browser UI snapshots.

Amodex keeps chat/project payloads out of public/shared HTTP caches. The browser
stores snapshot bodies in IndexedDB and sends the saved ETag explicitly during
background revalidation. A matching payload returns 304 with no JSON body.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

_PRIVATE_CACHE_HEADERS = {
    "Cache-Control": "private, no-cache, max-age=0, must-revalidate",
    "Vary": "Cookie",
    "X-Amodex-Cache": "private-revalidate",
}


def private_conditional_json(request: Request, payload: Any) -> Response:
    """Return a deterministic private JSON representation with ETag/304 support."""

    encoded = jsonable_encoder(payload)
    canonical = json.dumps(
        encoded,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    etag = f'"{hashlib.sha256(canonical).hexdigest()}"'
    headers = {**_PRIVATE_CACHE_HEADERS, "ETag": etag}

    if request.headers.get("if-none-match", "").strip() == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(content=encoded, headers=headers)
