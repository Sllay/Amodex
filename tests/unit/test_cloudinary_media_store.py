from __future__ import annotations

import hashlib
from urllib.parse import parse_qs

import httpx
import pytest

from amodex.multimodal.cloudinary_rest_store import CloudinaryAuthenticatedRestMediaStore
from amodex.multimodal.object_store import (
    CloudinaryMediaStore,
    FileMediaStore,
    MediaStoreError,
    UnavailableMediaStore,
    _cloudinary_signature,
    media_store_from_environment,
)


def _store_with_transport(handler, *, now: float = 1_700_000_000.0) -> CloudinaryMediaStore:
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return CloudinaryMediaStore(
        cloud_name="amodex-test",
        api_key="test-key",
        api_secret="test-secret",
        client=client,
        now=lambda: now,
    )


def _rest_store_with_transport(handler, *, now: float = 1_700_000_000.0) -> CloudinaryAuthenticatedRestMediaStore:
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return CloudinaryAuthenticatedRestMediaStore(
        cloud_name="amodex-test",
        api_key="test-key",
        api_secret="test-secret",
        client=client,
        now=lambda: now,
    )


def test_cloudinary_authenticated_rest_upload_uses_delivery_type_in_endpoint() -> None:
    data = b"private authenticated rest attachment"
    digest = hashlib.sha256(data).hexdigest()
    public_id = f"amodex/sha256/{digest[:2]}/{digest}.bin"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/raw/authenticated")
        assert not request.url.path.endswith("/raw/upload")
        assert public_id.encode() in request.content
        assert b'name="type"' not in request.content
        assert request.headers.get("authorization", "").startswith("Basic ")
        return httpx.Response(
            200,
            json={
                "public_id": public_id,
                "resource_type": "raw",
                "type": "authenticated",
            },
        )

    saved = _rest_store_with_transport(handler).put_bytes(data)
    assert saved.storage_ref == f"media://sha256/{digest}"
    assert saved.sha256 == digest
    assert saved.size_bytes == len(data)


def test_cloudinary_store_round_trip_preserves_opaque_sha_reference() -> None:
    data = b"private amodex attachment"
    digest = hashlib.sha256(data).hexdigest()
    public_id = f"amodex/sha256/{digest[:2]}/{digest}.bin"
    download_public_id = f"amodex/sha256/{digest[:2]}/{digest}"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert b"test-secret" not in request.content
        if request.url.path.endswith("/raw/upload"):
            assert request.method == "POST"
            assert public_id.encode() in request.content
            assert b"authenticated" in request.content
            return httpx.Response(
                200,
                json={
                    "public_id": public_id,
                    "resource_type": "raw",
                    "type": "authenticated",
                },
            )
        if request.url.path.endswith("/raw/download"):
            assert request.method == "GET"
            query = dict(request.url.params)
            assert query["public_id"] == download_public_id
            assert query["format"] == "bin"
            assert query["type"] == "authenticated"
            assert query["expires_at"] == "1700000300"
            assert query["api_key"] == "test-key"
            signed = {
                key: value
                for key, value in query.items()
                if key not in {"api_key", "signature"}
            }
            assert query["signature"] == _cloudinary_signature(signed, "test-secret")
            return httpx.Response(200, content=data)
        if request.url.path.endswith("/raw/destroy"):
            assert request.method == "POST"
            form = {key: values[-1] for key, values in parse_qs(request.content.decode()).items()}
            assert form["public_id"] == public_id
            assert form["type"] == "authenticated"
            assert form["api_key"] == "test-key"
            signed = {
                key: value
                for key, value in form.items()
                if key not in {"api_key", "signature"}
            }
            assert form["signature"] == _cloudinary_signature(signed, "test-secret")
            return httpx.Response(200, json={"result": "ok"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    store = _store_with_transport(handler)
    saved = store.put_bytes(data)
    assert saved.storage_ref == f"media://sha256/{digest}"
    assert saved.sha256 == digest
    assert saved.size_bytes == len(data)
    assert store.get_bytes(saved.storage_ref) == data
    store.delete(saved.storage_ref)
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests] == [
        "upload",
        "download",
        "destroy",
    ]


def test_cloudinary_store_rejects_tampered_download() -> None:
    expected = b"expected bytes"
    digest = hashlib.sha256(expected).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/raw/download")
        return httpx.Response(200, content=b"tampered bytes")

    store = _store_with_transport(handler)
    with pytest.raises(MediaStoreError, match="digest mismatch"):
        store.get_bytes(f"media://sha256/{digest}")


def test_cloudinary_store_rejects_invalid_reference_before_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(f"network must not be called: {request.url}")

    store = _store_with_transport(handler)
    with pytest.raises(MediaStoreError, match="invalid media storage reference"):
        store.get_bytes("media://sha256/../../etc/passwd")


def test_cloudinary_store_maps_missing_object_to_domain_error() -> None:
    data = b"missing"
    digest = hashlib.sha256(data).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"not found")

    store = _store_with_transport(handler)
    from amodex.multimodal.object_store import MediaObjectMissing

    with pytest.raises(MediaObjectMissing):
        store.get_bytes(f"media://sha256/{digest}")


def test_media_store_environment_keeps_file_default_backward_compatible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("AMODEX_MEDIA_STORE_BACKEND", raising=False)
    monkeypatch.setenv("AMODEX_MEDIA_STORE_DIR", str(tmp_path / "media"))
    assert isinstance(media_store_from_environment(), FileMediaStore)


def test_media_store_environment_requires_explicit_cloudinary_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AMODEX_MEDIA_STORE_BACKEND", raising=False)
    monkeypatch.delenv("AMODEX_MEDIA_STORE_DIR", raising=False)
    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://key:secret@amodex-test")
    assert isinstance(media_store_from_environment(), UnavailableMediaStore)

    monkeypatch.setenv("AMODEX_MEDIA_STORE_BACKEND", "cloudinary")
    configured = media_store_from_environment()
    assert isinstance(configured, CloudinaryMediaStore)
    assert configured.configured is True


def test_cloudinary_signature_matches_v2_scalar_contract() -> None:
    params = {
        "public_id": "amodex/sha256/aa/object.bin",
        "timestamp": "1700000000",
        "type": "authenticated",
    }
    source = "&".join(f"{key}={params[key]}" for key in sorted(params)) + "secret"
    expected = hashlib.sha1(source.encode("utf-8"), usedforsecurity=False).hexdigest()
    assert _cloudinary_signature(params, "secret") == expected
