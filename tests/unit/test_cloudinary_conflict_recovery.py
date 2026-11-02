from __future__ import annotations

import hashlib

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
        now=lambda: 1_700_000_000.0,
    )


def _assert_signed_raw_authenticated_upload(request: httpx.Request) -> None:
    assert request.method == "POST"
    assert request.url.path.endswith("/raw/upload")
    assert not request.url.path.endswith("/raw/authenticated")
    body = request.read()
    assert b'name="type"' in body
    assert b"authenticated" in body
    assert b'name="timestamp"' in body
    assert b"1700000000" in body
    assert b'name="api_key"' in body
    assert b"test-key" in body
    assert b'name="signature"' in body
    assert b'name="overwrite"' in body
    assert b"\r\n\r\nfalse\r\n" in body
    assert b"\r\n\r\n0\r\n" not in body


def test_authenticated_raw_upload_preserves_real_extension_in_public_id_and_ref() -> None:
    data = b"new private png file"
    digest = hashlib.sha256(data).hexdigest()
    public_id = f"amodex-production/sha256/{digest[:2]}/{digest}.png"

    def handler(request: httpx.Request) -> httpx.Response:
        _assert_signed_raw_authenticated_upload(request)
        body = request.read()
        assert public_id.encode() in body
        assert f'{digest}.png'.encode() in body
        assert f'{digest}.bin'.encode() not in body
        return httpx.Response(
            200,
            json={
                "public_id": public_id,
                "resource_type": "raw",
                "type": "authenticated",
                "bytes": len(data),
            },
        )

    saved = _store(handler).put_bytes(data, extension="png")
    assert saved.storage_ref == f"media://sha256/{digest}.png"
    assert saved.sha256 == digest
    assert saved.size_bytes == len(data)


def test_extension_aware_ref_downloads_with_matching_raw_format() -> None:
    data = b"stored png bytes"
    digest = hashlib.sha256(data).hexdigest()
    storage_ref = f"media://sha256/{digest}.png"
    expected_public_id = f"amodex-production/sha256/{digest[:2]}/{digest}"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path.endswith("/raw/download")
        assert request.url.params["public_id"] == expected_public_id
        assert request.url.params["format"] == "png"
        assert request.url.params["type"] == "authenticated"
        return httpx.Response(200, content=data)

    assert _store(handler).get_bytes(storage_ref) == data


def test_authenticated_raw_upload_400_surfaces_provider_message_and_redacts_credentials() -> None:
    data = b"bad request diagnostic"

    def handler(request: httpx.Request) -> httpx.Response:
        _assert_signed_raw_authenticated_upload(request)
        return httpx.Response(
            400,
            headers={"x-cld-error": "fallback diagnostic"},
            json={
                "error": {
                    "message": "Invalid value for overwrite; account=test-key secret=test-secret"
                }
            },
        )

    with pytest.raises(MediaStoreError) as raised:
        _store(handler).put_bytes(data, extension="png")

    message = str(raised.value)
    assert "HTTP 400" in message
    assert "Invalid value for overwrite" in message
    assert "test-key" not in message
    assert "test-secret" not in message
    assert "[redacted]" in message


def test_authenticated_rest_upload_409_is_idempotent_after_admin_collection_lookup() -> None:
    data = b"same private file retried"
    digest = hashlib.sha256(data).hexdigest()
    public_id = f"amodex-production/sha256/{digest[:2]}/{digest}.png"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            _assert_signed_raw_authenticated_upload(request)
            return httpx.Response(409, json={"error": {"message": "already exists"}})

        assert request.method == "GET"
        assert request.url.path.endswith("/resources/raw/authenticated")
        assert request.url.params.get_list("public_ids[]") == [public_id]
        assert request.url.params["max_results"] == "1"
        assert public_id not in request.url.path
        assert request.headers.get("authorization", "").startswith("Basic ")
        return httpx.Response(
            200,
            json={
                "resources": [
                    {
                        "public_id": public_id,
                        "resource_type": "raw",
                        "type": "authenticated",
                        "bytes": len(data),
                    }
                ]
            },
        )

    saved = _store(handler).put_bytes(data, extension="png")

    assert saved.storage_ref == f"media://sha256/{digest}.png"
    assert saved.sha256 == digest
    assert saved.size_bytes == len(data)
    assert [request.method for request in requests] == ["POST", "GET"]
    assert all(not request.url.path.endswith("/raw/download") for request in requests)


def test_authenticated_rest_upload_409_rejects_mismatched_existing_object() -> None:
    data = b"expected content"
    digest = hashlib.sha256(data).hexdigest()
    public_id = f"amodex-production/sha256/{digest[:2]}/{digest}.png"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            _assert_signed_raw_authenticated_upload(request)
            return httpx.Response(409, json={"error": {"message": "already exists"}})
        return httpx.Response(
            200,
            json={
                "resources": [
                    {
                        "public_id": public_id,
                        "resource_type": "raw",
                        "type": "authenticated",
                        "bytes": len(data) + 1,
                    }
                ]
            },
        )

    with pytest.raises(MediaStoreError, match="content-addressed object collision"):
        _store(handler).put_bytes(data, extension="png")


def test_authenticated_rest_upload_409_rejects_empty_admin_result() -> None:
    data = b"expected content"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            _assert_signed_raw_authenticated_upload(request)
            return httpx.Response(409, json={"error": {"message": "already exists"}})
        return httpx.Response(200, json={"resources": []})

    with pytest.raises(MediaStoreError, match="was not returned by the Admin API"):
        _store(handler).put_bytes(data, extension="png")


def test_authenticated_rest_upload_409_maps_admin_auth_failure_to_unavailable() -> None:
    data = b"private content"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            _assert_signed_raw_authenticated_upload(request)
            return httpx.Response(409, json={"error": {"message": "already exists"}})
        return httpx.Response(401, json={"error": {"message": "unauthorized"}})

    with pytest.raises(MediaStoreUnavailable, match="lookup is not authorized"):
        _store(handler).put_bytes(data, extension="png")


def test_authenticated_rest_upload_409_maps_admin_quota_failure_to_unavailable() -> None:
    data = b"private content"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            _assert_signed_raw_authenticated_upload(request)
            return httpx.Response(409, json={"error": {"message": "already exists"}})
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    with pytest.raises(MediaStoreUnavailable, match="lookup quota is unavailable"):
        _store(handler).put_bytes(data, extension="png")


def test_streaming_upload_route_maps_storage_failures_to_server_side_statuses() -> None:
    source = open("src/amodex/api/streaming_upload_routes.py", encoding="utf-8").read()
    unavailable = source.index("except MediaStoreUnavailable as exc:")
    generic = source.index("except MediaStoreError as exc:")
    assert unavailable < generic
    assert 'status_code=503' in source[unavailable:generic]
    assert '"MEDIA_STORE_UNAVAILABLE"' in source[unavailable:generic]
    assert 'status_code=502' in source[generic:]
    assert '"MEDIA_STORE_ERROR"' in source[generic:]
    assert 'logger.error(' in source[generic:]
    assert '"image/png": "png"' in source
    assert 'extension=extension' in source
    assert 'suffix=f".{extension}"' in source
