"""Bounded-memory media ingestion helpers.

Uploads arrive at the API as request chunks, are staged to a private temporary
file, and are then moved to the configured media backend without rebuilding the
entire object as a Python ``bytes`` value. Content addressing remains SHA-256.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from amodex.multimodal.object_store import (
    FileMediaStore,
    MediaObjectStore,
    MediaStoreError,
    MemoryMediaStore,
    StoredMediaObject,
)

_CHUNK_SIZE = 1024 * 1024


def hash_media_file(path: str | Path) -> tuple[str, int]:
    source = Path(path)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def put_media_file(
    store: MediaObjectStore,
    path: str | Path,
    *,
    extension: str | None = None,
) -> StoredMediaObject:
    """Persist a staged file using a streaming backend when available.

    Production Cloudinary exposes ``put_file`` and needs the original safe file
    extension for raw assets. Local stores continue to use the SHA-only opaque
    reference because their filesystem layout doesn't depend on an extension.
    """

    source = Path(path)
    digest, size_bytes = hash_media_file(source)
    put_file = getattr(store, "put_file", None)
    if callable(put_file):
        return put_file(
            source,
            digest=digest,
            size_bytes=size_bytes,
            extension=extension,
        )
    if isinstance(store, FileMediaStore):
        return _put_file_media_store(store, source, digest=digest, size_bytes=size_bytes)
    if isinstance(store, MemoryMediaStore):
        return store.put_bytes(source.read_bytes())
    raise MediaStoreError("configured media store does not support streaming file ingestion")


def _put_file_media_store(
    store: FileMediaStore,
    source: Path,
    *,
    digest: str,
    size_bytes: int,
) -> StoredMediaObject:
    storage_ref = f"media://sha256/{digest}"
    target = store._path_for_ref(storage_ref)
    if target.exists():
        existing_digest, existing_size = hash_media_file(target)
        if existing_digest != digest:
            raise MediaStoreError("content-addressed object digest mismatch")
        return StoredMediaObject(storage_ref, digest, existing_size)

    fd, tmp_name = tempfile.mkstemp(prefix=".amodex-media-", dir=str(target.parent))
    try:
        with source.open("rb") as input_handle, os.fdopen(fd, "wb") as output_handle:
            shutil.copyfileobj(input_handle, output_handle, length=_CHUNK_SIZE)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
        os.chmod(target, 0o600)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return StoredMediaObject(storage_ref, digest, size_bytes)
