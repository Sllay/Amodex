from __future__ import annotations

import hashlib
from urllib.parse import unquote

import httpx
import pytest

from amodex.multimodal.cloudinary_rest_store import CloudinaryAuthenticatedRestMediaStore
from amodex.multimodal.object_store import MediaStoreError, MediaStoreUnavailable


def _store(handler) -> CloudinaryAuthenticatedRestMediaStore:
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return CloudinaryAuthenticatedRestMediaStore(
        cloud_name="amodex-test",
        api_key="test-key",
        api_secret="test-secret",
        prefix="amodex-production",
        client=client,
    )


def test_authenticated_rest_upload_409_is_idempotent_after_admin_lookup() -> None:
    data = b"same private file retried"
    digest = hashlib.sha256(data).hexdigest()
    public_id = f"amodex-production/sha256/{digest[:2]}/{digest}.bin"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert request.url.path.endswith("/raw/authenticated")
            return httpx.Response(409, json={"error": {"message": "already exists"}})
        assert request.method == "GET"
        assert "/resources/raw/authenticated/" in request.url.path
        encoded = request.url.path.split("/resources/raw/authenticated/", 1)[1]
        assert unquote(encoded) == public_id
        assert request.headers.get("authorization", "").startswith("Basic ")
        return httpx.Response(
            200,
            json={
                "public_id": public_id,
                "resource_type": "raw",
                "type": "authenticated",
                "bytes": len(data),
            },
        )

    saved = _store(handler).put_bytes(data)

    assert saved.storage_ref == f"media://sha256/{digest}"
    assert saved.sha256 == digest
    assert saved.size_bytes == len(data)
    assert [request.method for request in requests] == ["POST", "GET"]
    assert all(not request.url.path.endswith("/raw/download") for request in requests)


def test_authenticated_rest_upload_409_rejects_mismatched_existing_object() -> None:
    data = b"expected content"
    digest = hashlib.sha256(data).hexdigest()
    public_id = f"amodex-production/sha256/{digest[:2]}/{digest}.bin"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(409, json={"error": {"message": "already exists"}})
        return httpx.Response(
            200,
            json={
                "public_id": public_id,
                "resource_type": "raw",
                "type": "authenticated",
                "bytes": len(data) + 1,
            },
        )

    with pytest.raises(MediaStoreError, match="content-addressed object collision"):
        _store(handler).put_bytes(data)


def test_authenticated_rest_upload_409_maps_admin_auth_failure_to_unavailable() -> None:
    data = b"private content"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(409, json={"error": {"message": "already exists"}})
        return httpx.Response(401, json={"error": {"message": "unauthorized"}})

    with pytest.raises(MediaStoreUnavailable, match="lookup is not authorized"):
        _store(handler).put_bytes(data)


def test_streaming_upload_route_maps_unavailable_before_generic_conflict() -> None:
    source = open("src/amodex/api/streaming_upload_routes.py", encoding="utf-8").read()
    unavailable = source.index("except MediaStoreUnavailable as exc:")
    generic = source.index("except MediaStoreError as exc:")
    assert unavailable < generic
    assert 'status_code=503' in source[unavailable:generic]
    assert '"MEDIA_STORE_UNAVAILABLE"' in source[unavailable:generic]
