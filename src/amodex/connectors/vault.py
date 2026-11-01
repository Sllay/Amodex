"""Connector credential vaults.

PostgreSQL stores only opaque credential references. Raw OAuth material lives behind
this interface so provider tokens never become ordinary durable product rows.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Protocol


class VaultUnavailable(RuntimeError):
    pass


class CredentialVault(Protocol):
    @property
    def available(self) -> bool: ...

    def put(self, value: dict[str, Any]) -> str: ...

    def get(self, ref: str) -> dict[str, Any]: ...

    def delete(self, ref: str) -> None: ...


class UnavailableCredentialVault:
    @property
    def available(self) -> bool:
        return False

    def put(self, value: dict[str, Any]) -> str:
        raise VaultUnavailable("connector credential vault is not configured")

    def get(self, ref: str) -> dict[str, Any]:
        raise VaultUnavailable("connector credential vault is not configured")

    def delete(self, ref: str) -> None:
        raise VaultUnavailable("connector credential vault is not configured")


class MemoryCredentialVault:
    """Test/local-process vault. Never suitable for durable deployment."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}

    @property
    def available(self) -> bool:
        return True

    def put(self, value: dict[str, Any]) -> str:
        ref = f"memory-vault://{secrets.token_hex(16)}"
        self._records[ref] = json.loads(json.dumps(value))
        return ref

    def get(self, ref: str) -> dict[str, Any]:
        if ref not in self._records:
            raise KeyError(ref)
        return json.loads(json.dumps(self._records[ref]))

    def delete(self, ref: str) -> None:
        self._records.pop(ref, None)


class FileCredentialVault:
    """Minimal zero-cost host vault with strict local file permissions.

    This is intentionally simple and auditable. A deployed server should place the
    directory on encrypted/private storage and never expose it through the web root.
    """

    _REF_RE = re.compile(r"^file-vault://([0-9a-f]{32})$")

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._root, 0o700)

    @property
    def available(self) -> bool:
        return True

    def put(self, value: dict[str, Any]) -> str:
        ident = secrets.token_hex(16)
        target = self._root / f"{ident}.json"
        temp = self._root / f".{ident}.{secrets.token_hex(8)}.tmp"
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
            os.chmod(target, 0o600)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        return f"file-vault://{ident}"

    def get(self, ref: str) -> dict[str, Any]:
        path = self._path_for(ref)
        return json.loads(path.read_text(encoding="utf-8"))

    def delete(self, ref: str) -> None:
        self._path_for(ref).unlink(missing_ok=True)

    def _path_for(self, ref: str) -> Path:
        match = self._REF_RE.fullmatch(ref)
        if not match:
            raise ValueError("invalid file-vault reference")
        path = (self._root / f"{match.group(1)}.json").resolve()
        if path.parent != self._root:
            raise ValueError("vault reference escaped root")
        return path


_ENV_REF_RE = re.compile(r"^env://([A-Z][A-Z0-9_]*)$")


def resolve_env_secret_ref(ref: str | None) -> str | None:
    if not ref:
        return None
    match = _ENV_REF_RE.fullmatch(ref)
    if not match:
        raise ValueError("secret reference must be env://UPPER_CASE_NAME")
    value = os.environ.get(match.group(1))
    if not value:
        raise VaultUnavailable(f"secret environment reference {ref} is unavailable")
    return value


def vault_from_environment() -> CredentialVault:
    root = os.environ.get("AMODEX_CONNECTOR_VAULT_DIR", "").strip()
    return FileCredentialVault(root) if root else UnavailableCredentialVault()
