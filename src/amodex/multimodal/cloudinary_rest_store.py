"""Cloudinary REST adapter that honors authenticated delivery semantics.

Cloudinary's REST Upload API encodes the delivery type in the endpoint path.
This adapter deliberately uploads raw objects through ``/raw/authenticated``
instead of sending ``type=authenticated`` to ``/raw/upload`` (the latter is an
SDK-style option and can result in a normal public ``upload`` asset when used
with raw REST calls).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

import httpx

from amodex.multimodal.object_store import (
    CloudinaryMediaStore,
    MediaStoreError,
    MediaStoreUnavailable,
    StoredMediaObject,
    _CLOUDINARY_API_ROOT,
    _cloudinary_json,
)


class CloudinaryAuthenticatedRestMediaStore(CloudinaryMediaStore):
    """Cloudinary store using the documented authenticated REST endpoint."""

    def put_bytes(self, data: bytes) -> StoredMediaObject:
        digest = hashlib.sha256(data).hexdigest()
        return self._upload(
            digest=digest,
            size_bytes=len(data),
            file_value=data,
        )

    def put_file(
        self,
        path: str | Path,
        *,
        digest: str | None = None,
        size_bytes: int | None = None,
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
        with source.open("rb") as input_handle:
            return self._upload(
                digest=digest,
                size_bytes=size_bytes,
                file_value=input_handle,
            )

    def _upload(
        self,
        *,
        digest: str,
        size_bytes: int,
        file_value: bytes | BinaryIO,
    ) -> StoredMediaObject:
        storage_ref = f"media://sha256/{digest}"
        public_id = self._public_id(digest)
        url = f"{_CLOUDINARY_API_ROOT}/{self.cloud_name}/raw/authenticated"

        try:
            response = self._client.post(
                url,
                auth=(self._api_key, self._api_secret),
                data={
                    "overwrite": "false",
                    "public_id": public_id,
                },
                files={
                    "file": (
                        f"{digest}.bin",
                        file_value,
                        "application/octet-stream",
                    )
                },
            )
        except httpx.HTTPError as exc:
            raise MediaStoreUnavailable("Cloudinary media upload is unavailable") from exc

        if response.status_code == 409:
            # Content-addressed uploads intentionally use overwrite=false. A retry
            # therefore receives 409 when the same SHA object already exists.
            # Verify the exact asset through the documented Admin API collection
            # filter. Using public_ids[] avoids embedding a slash-containing
            # public_id in the URL path, which can be mis-routed/decoded by HTTP
            # layers and previously produced false "resource missing" conflicts.
            self._verify_existing_object(public_id=public_id, size_bytes=size_bytes)
            return StoredMediaObject(storage_ref, digest, size_bytes)

        payload = _cloudinary_json(response, operation="upload")
        returned_public_id = str(payload.get("public_id", ""))
        if returned_public_id and returned_public_id != public_id:
            raise MediaStoreError("Cloudinary returned an unexpected public ID")
        if payload.get("resource_type") not in {None, "raw"}:
            raise MediaStoreError("Cloudinary returned an unexpected resource type")
        if payload.get("type") not in {None, "authenticated"}:
            raise MediaStoreError("Cloudinary returned an unexpected delivery type")
        return StoredMediaObject(storage_ref, digest, size_bytes)

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
