from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

import httpx
import pytest
from psycopg import pq

from amodex.api import streaming_upload_routes
from amodex.multimodal.cloudinary_rest_store import CloudinaryAuthenticatedRestMediaStore
from amodex.multimodal.object_store import FileMediaStore
from amodex.multimodal.streaming_store import put_media_file
from amodex.storage.runtime_pool import RuntimeConnectionPool, RuntimePoolExhausted


class _FakeInfo:
    transaction_status = pq.TransactionStatus.IDLE


class _FakeConnection:
    def __init__(self) -> None:
        self.closed = False
        self.info = _FakeInfo()
        self.row_factory = None
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def test_runtime_pool_reuses_connections_and_enforces_two_connection_cap() -> None:
    created: list[_FakeConnection] = []

    def connect(_dsn: str) -> _FakeConnection:
        conn = _FakeConnection()
        created.append(conn)
        return conn

    pool = RuntimeConnectionPool(
        "postgresql://runtime-test",
        max_size=2,
        timeout_seconds=0.01,
        connect_factory=connect,
    )
    first = pool.acquire()
    second = pool.acquire()
    assert len(created) == 2
    assert pool.physical_count == 2

    with pytest.raises(RuntimePoolExhausted):
        pool.acquire()
    assert len(created) == 2

    pool.release(first)
    recycled = pool.acquire()
    assert recycled is first
    assert len(created) == 2

    pool.release(second)
    pool.release(recycled)
    assert pool.available_count == 2
    pool.close()
    assert all(conn.closed for conn in created)


def test_runtime_pool_context_preserves_commit_and_rollback_semantics() -> None:
    created: list[_FakeConnection] = []

    def connect(_dsn: str) -> _FakeConnection:
        conn = _FakeConnection()
        created.append(conn)
        return conn

    pool = RuntimeConnectionPool(
        "postgresql://runtime-test",
        max_size=1,
        connect_factory=connect,
    )
    with pool.connect() as first:
        assert first is created[0]
    assert created[0].commits == 1

    with pytest.raises(RuntimeError, match="boom"):
        with pool.connect() as second:
            assert second is created[0]
            raise RuntimeError("boom")
    assert created[0].rollbacks == 1
    assert len(created) == 1
    pool.close()


def test_file_media_ingestion_is_chunked_and_does_not_call_read_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data = (b"amodex-streaming-file-" * 80_000) + b"tail"
    source = tmp_path / "source.bin"
    source.write_bytes(data)
    store = FileMediaStore(tmp_path / "media")

    def forbidden_read_bytes(_self: Path) -> bytes:
        raise AssertionError("streaming ingestion must not call Path.read_bytes")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    saved = put_media_file(store, source)
    digest = hashlib.sha256(data).hexdigest()
    assert saved.sha256 == digest
    assert saved.storage_ref == f"media://sha256/{digest}"
    assert saved.size_bytes == len(data)

    target = store._path_for_ref(saved.storage_ref)
    with target.open("rb") as handle:
        assert hashlib.sha256(handle.read()).hexdigest() == digest


def test_cloudinary_authenticated_store_accepts_file_stream_without_read_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data = (b"cloudinary-stream-" * 100_000) + b"tail"
    source = tmp_path / "cloudinary.bin"
    source.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    public_id = f"amodex/sha256/{digest[:2]}/{digest}.bin"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path.endswith("/raw/authenticated")
        assert request.headers.get("authorization", "").startswith("Basic ")
        return httpx.Response(
            200,
            json={
                "public_id": public_id,
                "resource_type": "raw",
                "type": "authenticated",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    store = CloudinaryAuthenticatedRestMediaStore(
        cloud_name="amodex-test",
        api_key="test-key",
        api_secret="test-secret",
        client=client,
    )

    def forbidden_read_bytes(_self: Path) -> bytes:
        raise AssertionError("Cloudinary file upload must not call Path.read_bytes")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    saved = put_media_file(store, source)
    assert saved.sha256 == digest
    assert saved.size_bytes == len(data)
    assert len(seen) == 1


def test_streaming_upload_route_never_builds_whole_request_bytearray() -> None:
    source = inspect.getsource(streaming_upload_routes)
    assert "async for chunk in request.stream()" in source
    assert "bytearray(" not in source
    assert "put_media_file(media_store" in source
    assert "tempfile.mkstemp" in source


def test_workspace_runtime_wires_pool_and_replaces_legacy_upload_route() -> None:
    source = Path("src/amodex/api/workspace_app.py").read_text(encoding="utf-8")
    assert "install_runtime_connection_pool()" in source
    assert "_remove_legacy_upload_route(multimodal_router)" in source
    assert "build_streaming_upload_router(" in source
    assert "close_runtime_connection_pools" in source
