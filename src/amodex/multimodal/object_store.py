"""Bounded media object storage for Step 3 binary attachments.

The database stores only opaque media references and metadata. Bytes stay behind a
content-addressed store. The zero-cost durable implementation is filesystem based
and must be pointed at a private directory by the operator.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import tempfile
from typing import Protocol

_MEDIA_REF_RE = re.compile(r"^media://sha256/([0-9a-f]{64})$")


class MediaStoreError(RuntimeError):
    pass


class MediaStoreUnavailable(MediaStoreError):
    pass


class MediaObjectMissing(MediaStoreError):
    pass


@dataclass(frozen=True, slots=True)
class StoredMediaObject:
    storage_ref: str
    sha256: str
    size_bytes: int


class MediaObjectStore(Protocol):
    @property
    def configured(self) -> bool: ...

    def put_bytes(self, data: bytes) -> StoredMediaObject: ...

    def get_bytes(self, storage_ref: str) -> bytes: ...

    def delete(self, storage_ref: str) -> None: ...


class UnavailableMediaStore:
    @property
    def configured(self) -> bool:
        return False

    def put_bytes(self, data: bytes) -> StoredMediaObject:
        raise MediaStoreUnavailable("AMODEX_MEDIA_STORE_DIR is not configured")

    def get_bytes(self, storage_ref: str) -> bytes:
        raise MediaStoreUnavailable("AMODEX_MEDIA_STORE_DIR is not configured")

    def delete(self, storage_ref: str) -> None:
        raise MediaStoreUnavailable("AMODEX_MEDIA_STORE_DIR is not configured")


class MemoryMediaStore:
    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    @property
    def configured(self) -> bool:
        return True

    def put_bytes(self, data: bytes) -> StoredMediaObject:
        digest = hashlib.sha256(data).hexdigest()
        ref = f"media://sha256/{digest}"
        self._objects.setdefault(ref, bytes(data))
        return StoredMediaObject(ref, digest, len(data))

    def get_bytes(self, storage_ref: str) -> bytes:
        try:
            return self._objects[storage_ref]
        except KeyError as exc:
            raise MediaObjectMissing(storage_ref) from exc

    def delete(self, storage_ref: str) -> None:
        self._objects.pop(storage_ref, None)


class FileMediaStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    @property
    def configured(self) -> bool:
        return True

    def _path_for_ref(self, storage_ref: str) -> Path:
        match = _MEDIA_REF_RE.fullmatch(storage_ref)
        if not match:
            raise MediaStoreError("invalid media storage reference")
        digest = match.group(1)
        bucket = self.root / "sha256" / digest[:2]
        bucket.mkdir(parents=True, exist_ok=True)
        os.chmod(bucket.parent, 0o700)
        os.chmod(bucket, 0o700)
        path = (bucket / digest).resolve()
        if self.root not in path.parents:
            raise MediaStoreError("media path escaped configured root")
        return path

    def put_bytes(self, data: bytes) -> StoredMediaObject:
        digest = hashlib.sha256(data).hexdigest()
        ref = f"media://sha256/{digest}"
        path = self._path_for_ref(ref)
        if path.exists():
            existing = path.read_bytes()
            if hashlib.sha256(existing).hexdigest() != digest:
                raise MediaStoreError("content-addressed object digest mismatch")
            return StoredMediaObject(ref, digest, len(existing))

        fd, tmp_name = tempfile.mkstemp(prefix=".amodex-media-", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, path)
            os.chmod(path, 0o600)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        return StoredMediaObject(ref, digest, len(data))

    def get_bytes(self, storage_ref: str) -> bytes:
        path = self._path_for_ref(storage_ref)
        if not path.is_file():
            raise MediaObjectMissing(storage_ref)
        data = path.read_bytes()
        expected = _MEDIA_REF_RE.fullmatch(storage_ref)
        assert expected is not None
        if hashlib.sha256(data).hexdigest() != expected.group(1):
            raise MediaStoreError("stored media digest mismatch")
        return data

    def delete(self, storage_ref: str) -> None:
        path = self._path_for_ref(storage_ref)
        if path.exists():
            path.unlink()


def media_store_from_environment() -> MediaObjectStore:
    root = os.environ.get("AMODEX_MEDIA_STORE_DIR", "").strip()
    if not root:
        return UnavailableMediaStore()
    return FileMediaStore(root)


def max_upload_bytes_from_environment() -> int:
    raw = os.environ.get("AMODEX_MEDIA_MAX_UPLOAD_BYTES", "26214400").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("AMODEX_MEDIA_MAX_UPLOAD_BYTES must be an integer") from exc
    if value < 1 or value > 104_857_600:
        raise ValueError("AMODEX_MEDIA_MAX_UPLOAD_BYTES must be between 1 and 104857600")
    return value
