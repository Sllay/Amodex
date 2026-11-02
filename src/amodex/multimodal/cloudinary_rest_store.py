"""Cloudinary REST adapter that honors authenticated delivery semantics.

Cloudinary's REST Upload API encodes the delivery type in the endpoint path.
This adapter deliberately uploads raw objects through ``/raw/authenticated``
instead of sending ``type=authenticated`` to ``/raw/upload`` (the latter is an
SDK-style option and can result in a normal public ``upload`` asset when used
with raw REST calls).
"""

from __future__ import annotations

import hashlib

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
                files={"file": (f"{digest}.bin", data, "application/octet-stream")},
            )
        except httpx.HTTPError as exc:
            raise MediaStoreUnavailable("Cloudinary media upload is unavailable") from exc

        if response.status_code == 409:
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
