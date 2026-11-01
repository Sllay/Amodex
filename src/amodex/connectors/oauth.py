"""OAuth/PKCE helpers and connector configuration.

Raw OAuth state is returned to the browser and only its SHA-256 digest is stored.
PKCE verifiers are generated locally and stored through CredentialVault references.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass


def new_oauth_state() -> str:
    return secrets.token_urlsafe(32)


def hash_oauth_state(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def new_pkce_verifier() -> str:
    # token_urlsafe(64) produces only unreserved URL characters and comfortably
    # stays inside the OAuth PKCE verifier size envelope.
    verifier = secrets.token_urlsafe(64)
    return verifier[:96]


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _csv(value: str | None, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if not value:
        return default
    return tuple(part.strip() for part in value.replace(" ", ",").split(",") if part.strip())


@dataclass(frozen=True, slots=True)
class GitHubAppConfig:
    app_slug: str
    client_id: str
    redirect_uri: str
    client_secret_ref: str | None = None

    @classmethod
    def from_environment(cls) -> "GitHubAppConfig | None":
        slug = os.environ.get("AMODEX_GITHUB_APP_SLUG", "").strip()
        client_id = os.environ.get("AMODEX_GITHUB_APP_CLIENT_ID", "").strip()
        redirect_uri = os.environ.get("AMODEX_GITHUB_APP_REDIRECT_URI", "").strip()
        if not (slug and client_id and redirect_uri):
            return None
        return cls(
            app_slug=slug,
            client_id=client_id,
            redirect_uri=redirect_uri,
            client_secret_ref=os.environ.get("AMODEX_GITHUB_APP_CLIENT_SECRET_REF") or None,
        )


@dataclass(frozen=True, slots=True)
class RobloxOAuthConfig:
    client_id: str
    redirect_uri: str
    scopes: tuple[str, ...]
    client_secret_ref: str | None = None

    @classmethod
    def from_environment(cls) -> "RobloxOAuthConfig | None":
        client_id = os.environ.get("AMODEX_ROBLOX_OAUTH_CLIENT_ID", "").strip()
        redirect_uri = os.environ.get("AMODEX_ROBLOX_OAUTH_REDIRECT_URI", "").strip()
        if not (client_id and redirect_uri):
            return None
        return cls(
            client_id=client_id,
            redirect_uri=redirect_uri,
            scopes=_csv(
                os.environ.get("AMODEX_ROBLOX_OAUTH_SCOPES"),
                default=("openid", "profile"),
            ),
            client_secret_ref=os.environ.get("AMODEX_ROBLOX_OAUTH_CLIENT_SECRET_REF") or None,
        )
