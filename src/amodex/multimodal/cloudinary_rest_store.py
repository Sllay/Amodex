"""Cloudinary REST adapter for private content-addressed raw assets.

Cloudinary raw uploads use ``/raw/upload`` and carry the authenticated delivery
option in the signed upload payload. Raw assets must retain a real extension in
their public ID, while the SHA-256 digest remains the content authority.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import BinaryIO

import httpx

from amodex.multimodal.object_store import (
    CloudinaryMediaStore,
    MediaObjectMissing,
    MediaStoreError,
    MediaStoreUnavailable,
    StoredMediaObject,
    _CLOUDINARY_API_ROOT,
    _cloudinary_json,
    _raise_cloudinary_status,
)

_MEDIA_REF_WITH_EXTENSION_RE = re.compile(
    r"^media://sha256/([0-9a-f]{64})(?:\.([a-z0-9]{1,16}))?$"
)
_SAFE_EXTENSION_RE = re.compile(r"^[a-z0-9]{1,16}$")


def _cloudinary_error_detail(response: httpx.Response) -> str:
    """Extract a bounded provider diagnostic without logging request credentials."""

    message = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message", "")).strip()
    if not message:
        message = response.headers.get("x-cld-error", "").strip()
    return " ".join(message.split())[:320]


def _normalize_extension(extension: str | None) -> str:
    value = (extension or "dat").strip().lower().lstrip(".")
    if not value:
        value = "dat"
    if not _SAFE_EXTENSION_RE.fullmatch(value):
        raise MediaStoreError("invalid media file extension")
    return value


def _storage_ref_parts(storage_ref: str) -> tuple[str, str]:
    match = _MEDIA_REF_WITH_EXTENSION_RE.fullmatch(storage_ref)
    if not match:
        raise MediaStoreError("invalid media storage reference")
    # Legacy refs did not carry an extension and historically pointed at .bin.
    return match.group(1), (match.group(2) or "bin")


class CloudinaryAuthenticatedRestMediaStore(CloudinaryMediaStore):
    """Cloudinary store using signed raw uploads with authenticated delivery."""

    def put_bytes(
        self,
        data: bytes,
        *,
        extension: str | None = None,
    ) -> StoredMediaObject:
        digest = hashlib.sha256(data).hexdigest()
        return self._upload(
            digest=digest,
            size_bytes=len(data),
            file_value=data,
            extension=_normalize_extension(extension),
        )

    def put_file(
        self,
        path: str | Path,
        *,
        digest: str | None = None,
        size_bytes: int | None = None,
        extension: str | None = None,
    ) -> StoredMediaObject:
        """Upload a staged file without materializing the whole object in RAM."""

        source = Path(path)
        if digest is None:
            hasher = hashlib.sha256()
            measured_size = 0
            with source.open("rb") as input_handle:
                while chunk := input_handle.read(1024 * 1024):
                    hasher.update(chunk)
                    measured_size += len(chunk)
            digest = hasher.hexdigest()
            size_bytes = measured_size
        elif size_bytes is None:
            size_bytes = source.stat().st_size

        assert size_bytes is not None
        chosen_extension = _normalize_extension(extension or source.suffix)
        with source.open("rb") as input_handle:
            return self._upload(
                digest=digest,
                size_bytes=size_bytes,
                file_value=input_handle,
                extension=chosen_extension,
            )

    def _public_id_with_extension(self, digest: str, extension: str) -> str:
        return f"{self.prefix}/sha256/{digest[:2]}/{digest}.{extension}"

    def _upload(
        self,
        *,
        digest: str,
        size_bytes: int,
        file_value: bytes | BinaryIO,
        extension: str,
    ) -> StoredMediaObject:
        storage_ref = f"media://sha256/{digest}.{extension}"
        public_id = self._public_id_with_extension(digest, extension)
        url = f"{_CLOUDINARY_API_ROOT}/{self.cloud_name}/raw/upload"
        params = self._signed_params(
            {
                "overwrite": "false",
                "public_id": public_id,
                "timestamp": str(int(self._now())),
                "type": "authenticated",
            }
        )

        try:
            response = self._client.post(
                url,
                data=params,
                files={
                    "file": (
                        f"{digest}.{extension}",
                        file_value,
                        "application/octet-stream",
                    )
                },
            )
        except httpx.HTTPError as exc:
            raise MediaStoreUnavailable("Cloudinary media upload is unavailable") from exc

        if response.status_code == 409:
            self._verify_existing_object(public_id=public_id, size_bytes=size_bytes)
            return StoredMediaObject(storage_ref, digest, size_bytes)

        if response.status_code >= 400 and response.status_code not in {401, 403, 420, 429}:
            detail = _cloudinary_error_detail(response)
            if detail:
                detail = detail.replace(self._api_key, "[redacted]")
                detail = detail.replace(self._api_secret, "[redacted]")
                raise MediaStoreError(
                    f"Cloudinary media upload failed with HTTP {response.status_code}: {detail}"
                )

        payload = _cloudinary_json(response, operation="upload")
        returned_public_id = str(payload.get("public_id", ""))
        if returned_public_id and returned_public_id != public_id:
            raise MediaStoreError("Cloudinary returned an unexpected public ID")
        if payload.get("resource_type") not in {None, "raw"}:
            raise MediaStoreError("Cloudinary returned an unexpected resource type")
        if payload.get("type") not in {None, "authenticated"}:
            raise MediaStoreError("Cloudinary returned an unexpected delivery type")
        return StoredMediaObject(storage_ref, digest, size_bytes)

    def get_bytes(self, storage_ref: str) -> bytes:
        digest, extension = _storage_ref_parts(storage_ref)
        timestamp = int(self._now())
        # For raw assets the extension is part of the Cloudinary public_id itself.
        # private_download_url also requires the format as a separate signed field,
        # so preserve the raw public_id exactly as it was created during upload.
        params = self._signed_params(
            {
                "expires_at": str(timestamp + 300),
                "format": extension,
                "public_id": self._public_id_with_extension(digest, extension),
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
        digest, extension = _storage_ref_parts(storage_ref)
        params = self._signed_params(
            {
                "public_id": self._public_id_with_extension(digest, extension),
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

    def _verify_existing_object(self, *, public_id: str, size_bytes: int) -> None:
        url = f"{_CLOUDINARY_API_ROOT}/{self.cloud_name}/resources/raw/authenticated"
        try:
            response = self._client.get(
                url,
                auth=(self._api_key, self._api_secret),
                params=[
                    ("public_ids[]", public_id),
                    ("max_results", "1"),
                ],
            )
        except httpx.HTTPError as exc:
            raise MediaStoreUnavailable("Cloudinary media lookup is unavailable") from exc

        if response.status_code in {401, 403}:
            raise MediaStoreUnavailable("Cloudinary media lookup is not authorized")
        if response.status_code in {420, 429}:
            raise MediaStoreUnavailable("Cloudinary media lookup quota is unavailable")

        payload = _cloudinary_json(response, operation="lookup")
        resources = payload.get("resources")
        if not isinstance(resources, list):
            raise MediaStoreError("Cloudinary media lookup returned an invalid response")

        resource = next(
            (
                item
                for item in resources
                if isinstance(item, dict) and str(item.get("public_id", "")) == public_id
            ),
            None,
        )
        if resource is None:
            raise MediaStoreError(
                "Cloudinary reported an upload conflict but the content-addressed object "
                "was not returned by the Admin API"
            )

        if resource.get("resource_type") not in {None, "raw"}:
            raise MediaStoreError("Cloudinary content-addressed object collision")
        if resource.get("type") not in {None, "authenticated"}:
            raise MediaStoreError("Cloudinary content-addressed object collision")

        remote_size = resource.get("bytes")
        if remote_size is not None:
            try:
                measured_size = int(remote_size)
            except (TypeError, ValueError) as exc:
                raise MediaStoreError("Cloudinary returned an invalid object size") from exc
            if measured_size != size_bytes:
                raise MediaStoreError("Cloudinary content-addressed object collision")
