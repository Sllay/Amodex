"""Bounded media object storage for binary attachments.

The database stores only opaque ``media://sha256/...`` references and metadata.
Bytes stay behind a replaceable content-addressed store. Local development can
use a private filesystem while production can use authenticated Cloudinary raw
assets without exposing Cloudinary URLs as application authority.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Protocol
from urllib.parse import unquote, urlparse

import httpx

_MEDIA_REF_RE = re.compile(r"^media://sha256/([0-9a-f]{64})$")
_CLOUDINARY_CLOUD_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_CLOUDINARY_PREFIX_RE = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$")
_CLOUDINARY_API_ROOT = "https://api.cloudinary.com/v1_1"
_CLOUDINARY_OBJECT_FORMAT = "bin"


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
        raise MediaStoreUnavailable("media object store is not configured")

    def get_bytes(self, storage_ref: str) -> bytes:
        raise MediaStoreUnavailable("media object store is not configured")

    def delete(self, storage_ref: str) -> None:
        raise MediaStoreUnavailable("media object store is not configured")


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
        digest = _digest_from_ref(storage_ref)
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
        expected = _digest_from_ref(storage_ref)
        if hashlib.sha256(data).hexdigest() != expected:
            raise MediaStoreError("stored media digest mismatch")
        return data

    def delete(self, storage_ref: str) -> None:
        path = self._path_for_ref(storage_ref)
        if path.exists():
            path.unlink()


class CloudinaryMediaStore:
    """Private content-addressed storage backed by Cloudinary raw assets.

    Every object is uploaded as ``raw`` + ``authenticated``. The application's
    opaque ``media://sha256/...`` reference remains the authority; Cloudinary
    public IDs and signed download URLs are deterministic implementation details.
    """

    def __init__(
        self,
        *,
        cloud_name: str,
        api_key: str,
        api_secret: str,
        prefix: str = "amodex",
        client: httpx.Client | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        cloud_name = cloud_name.strip()
        api_key = api_key.strip()
        api_secret = api_secret.strip()
        prefix = prefix.strip().strip("/")
        if not _CLOUDINARY_CLOUD_RE.fullmatch(cloud_name):
            raise ValueError("invalid Cloudinary cloud name")
        if not api_key or not api_secret:
            raise ValueError("Cloudinary API key and secret are required")
        if not _CLOUDINARY_PREFIX_RE.fullmatch(prefix):
            raise ValueError("invalid Cloudinary object prefix")
        self.cloud_name = cloud_name
        self._api_key = api_key
        self._api_secret = api_secret
        self.prefix = prefix
        self._client = client or httpx.Client(timeout=30.0, follow_redirects=True)
        self._now = now or time.time

    @property
    def configured(self) -> bool:
        return True

    def _public_id(self, digest: str) -> str:
        # Raw Cloudinary public IDs include the extension.
        return f"{self.prefix}/sha256/{digest[:2]}/{digest}.{_CLOUDINARY_OBJECT_FORMAT}"

    def _download_public_id(self, digest: str) -> str:
        # private_download_url accepts public_id and format separately.
        return f"{self.prefix}/sha256/{digest[:2]}/{digest}"

    def _signed_params(self, params: dict[str, str]) -> dict[str, str]:
        normalized = {key: value for key, value in params.items() if value != ""}
        normalized["signature"] = _cloudinary_signature(normalized, self._api_secret)
        normalized["api_key"] = self._api_key
        return normalized

    def put_bytes(self, data: bytes) -> StoredMediaObject:
        digest = hashlib.sha256(data).hexdigest()
        storage_ref = f"media://sha256/{digest}"
        public_id = self._public_id(digest)
        timestamp = str(int(self._now()))
        params = self._signed_params(
            {
                "overwrite": "0",
                "public_id": public_id,
                "timestamp": timestamp,
                "type": "authenticated",
            }
        )
        url = f"{_CLOUDINARY_API_ROOT}/{self.cloud_name}/raw/upload"
        try:
            response = self._client.post(
                url,
                data=params,
                files={"file": (f"{digest}.bin", data, "application/octet-stream")},
            )
        except httpx.HTTPError as exc:
            raise MediaStoreUnavailable("Cloudinary media upload is unavailable") from exc

        if response.status_code in {409}:
            existing = self.get_bytes(storage_ref)
            if hashlib.sha256(existing).hexdigest() != digest:
                raise MediaStoreError("Cloudinary content-addressed object collision")
            return StoredMediaObject(storage_ref, digest, len(existing))
        payload = _cloudinary_json(response, operation="upload")
        returned_public_id = str(payload.get("public_id", ""))
        if returned_public_id and returned_public_id != public_id:
            raise MediaStoreError("Cloudinary returned an unexpected public ID")
        if payload.get("resource_type") not in {None, "raw"}:
            raise MediaStoreError("Cloudinary returned an unexpected resource type")
        if payload.get("type") not in {None, "authenticated"}:
            raise MediaStoreError("Cloudinary returned an unexpected delivery type")
        return StoredMediaObject(storage_ref, digest, len(data))

    def get_bytes(self, storage_ref: str) -> bytes:
        digest = _digest_from_ref(storage_ref)
        timestamp = int(self._now())
        params = self._signed_params(
            {
                "expires_at": str(timestamp + 300),
                "format": _CLOUDINARY_OBJECT_FORMAT,
                "public_id": self._download_public_id(digest),
                "timestamp": str(timestamp),
                "type": "authenticated",
            }
        )
        url = f"{_CLOUDINARY_API_ROOT}/{self.cloud_name}/raw/download"
        try:
            response = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise MediaStoreUnavailable("Cloudinary media download is unavailable") from exc
        if response.status_code == 404:
            raise MediaObjectMissing(storage_ref)
        _raise_cloudinary_status(response, operation="download")
        data = response.content
        if hashlib.sha256(data).hexdigest() != digest:
            raise MediaStoreError("Cloudinary stored media digest mismatch")
        return data

    def delete(self, storage_ref: str) -> None:
        digest = _digest_from_ref(storage_ref)
        params = self._signed_params(
            {
                "public_id": self._public_id(digest),
                "timestamp": str(int(self._now())),
                "type": "authenticated",
            }
        )
        url = f"{_CLOUDINARY_API_ROOT}/{self.cloud_name}/raw/destroy"
        try:
            response = self._client.post(url, data=params)
        except httpx.HTTPError as exc:
            raise MediaStoreUnavailable("Cloudinary media delete is unavailable") from exc
        if response.status_code == 404:
            return
        payload = _cloudinary_json(response, operation="delete")
        result = str(payload.get("result", ""))
        if result not in {"ok", "not found"}:
            raise MediaStoreError("Cloudinary media delete did not complete")

    @classmethod
    def from_environment(
        cls,
        *,
        client: httpx.Client | None = None,
        now: Callable[[], float] | None = None,
    ) -> "CloudinaryMediaStore":
        cloud_name, api_key, api_secret = _cloudinary_credentials_from_environment()
        prefix = os.environ.get("AMODEX_CLOUDINARY_PREFIX", "amodex")
        return cls(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
            prefix=prefix,
            client=client,
            now=now,
        )


def _digest_from_ref(storage_ref: str) -> str:
    match = _MEDIA_REF_RE.fullmatch(storage_ref)
    if not match:
        raise MediaStoreError("invalid media storage reference")
    return match.group(1)


def _cloudinary_signature(params: dict[str, str], api_secret: str) -> str:
    # Cloudinary API signature v2 sorts key=value pairs and escapes ampersands
    # before appending the secret. We intentionally use only scalar parameters.
    pieces = []
    for key, value in params.items():
        if value == "":
            continue
        pieces.append(f"{key}={value}".replace("&", "%26"))
    source = "&".join(sorted(pieces)) + api_secret
    return hashlib.sha1(source.encode("utf-8"), usedforsecurity=False).hexdigest()


def _raise_cloudinary_status(response: httpx.Response, *, operation: str) -> None:
    if response.status_code < 400:
        return
    if response.status_code in {401, 403}:
        raise MediaStoreUnavailable(f"Cloudinary media {operation} is not authorized")
    if response.status_code in {420, 429}:
        raise MediaStoreUnavailable(f"Cloudinary media {operation} quota is unavailable")
    raise MediaStoreError(f"Cloudinary media {operation} failed with HTTP {response.status_code}")


def _cloudinary_json(response: httpx.Response, *, operation: str) -> dict[str, object]:
    _raise_cloudinary_status(response, operation=operation)
    try:
        payload = response.json()
    except ValueError as exc:
        raise MediaStoreError(f"Cloudinary media {operation} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise MediaStoreError(f"Cloudinary media {operation} returned an invalid response")
    if "error" in payload:
        raise MediaStoreError(f"Cloudinary media {operation} returned an API error")
    return payload


def _cloudinary_credentials_from_environment() -> tuple[str, str, str]:
    cloudinary_url = os.environ.get("CLOUDINARY_URL", "").strip()
    if cloudinary_url:
        parsed = urlparse(cloudinary_url)
        if parsed.scheme != "cloudinary" or not parsed.username or not parsed.password or not parsed.hostname:
            raise ValueError("CLOUDINARY_URL must use cloudinary://<key>:<secret>@<cloud_name>")
        return parsed.hostname, unquote(parsed.username), unquote(parsed.password)

    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
    api_key = os.environ.get("CLOUDINARY_API_KEY", "").strip()
    api_secret = os.environ.get("CLOUDINARY_API_SECRET", "").strip()
    if not cloud_name or not api_key or not api_secret:
        raise ValueError(
            "Cloudinary backend requires CLOUDINARY_URL or CLOUDINARY_CLOUD_NAME, "
            "CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET"
        )
    return cloud_name, api_key, api_secret


def media_store_from_environment() -> MediaObjectStore:
    backend = os.environ.get("AMODEX_MEDIA_STORE_BACKEND", "").strip().lower()
    root = os.environ.get("AMODEX_MEDIA_STORE_DIR", "").strip()

    # Preserve the previous local behavior when no backend is explicitly chosen.
    if not backend:
        return FileMediaStore(root) if root else UnavailableMediaStore()
    if backend in {"file", "filesystem"}:
        if not root:
            raise ValueError("file media backend requires AMODEX_MEDIA_STORE_DIR")
        return FileMediaStore(root)
    if backend == "cloudinary":
        return CloudinaryMediaStore.from_environment()
    if backend in {"none", "disabled", "unavailable"}:
        return UnavailableMediaStore()
    raise ValueError(f"unsupported AMODEX_MEDIA_STORE_BACKEND: {backend}")


def max_upload_bytes_from_environment() -> int:
    raw = os.environ.get("AMODEX_MEDIA_MAX_UPLOAD_BYTES", "26214400").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("AMODEX_MEDIA_MAX_UPLOAD_BYTES must be an integer") from exc
    if value < 1 or value > 104_857_600:
        raise ValueError("AMODEX_MEDIA_MAX_UPLOAD_BYTES must be between 1 and 104857600")
    return value
