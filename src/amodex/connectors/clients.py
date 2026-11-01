"""Provider clients for GitHub App and Roblox Open Cloud OAuth.

Both clients are dependency-injected around httpx so CI can test every OAuth and
resource path without live user credentials. Raw tokens only enter CredentialVault.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from amodex.connectors.models import (
    ConnectorAccountRecord,
    ConnectorProvider,
    ConnectorRefreshResponse,
    ConnectorResourceRecord,
    ConnectorResourceType,
    ConnectorStartResponse,
)
from amodex.connectors.oauth import (
    GitHubAppConfig,
    RobloxOAuthConfig,
    new_oauth_state,
    new_pkce_verifier,
    pkce_challenge,
)
from amodex.connectors.vault import CredentialVault, resolve_env_secret_ref
from amodex.storage.connectors import ConnectorStateError, PostgresConnectorRepository


class ConnectorConfigurationError(RuntimeError):
    pass


class GitHubAppConnector:
    AUTH_URL = "https://github.com/login/oauth/authorize"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    API = "https://api.github.com"

    def __init__(
        self,
        *,
        config: GitHubAppConfig | None,
        repository: PostgresConnectorRepository,
        vault: CredentialVault,
        http: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.vault = vault
        self.http = http or httpx.Client(timeout=20.0, follow_redirects=True)

    @property
    def configured(self) -> bool:
        return self.config is not None and self.vault.available

    def start(self, *, workspace_id: str) -> ConnectorStartResponse:
        cfg = self._require_config()
        state = new_oauth_state()
        verifier = new_pkce_verifier()
        verifier_ref = self.vault.put({"verifier": verifier})
        expires = self.repository.create_oauth_state(
            raw_state=state,
            workspace_id=workspace_id,
            provider=ConnectorProvider.GITHUB,
            verifier_ref=verifier_ref,
            requested_scopes=(),
            redirect_uri=cfg.redirect_uri,
        )
        query = urlencode(
            {
                "client_id": cfg.client_id,
                "redirect_uri": cfg.redirect_uri,
                "state": state,
                "code_challenge": pkce_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        install = f"https://github.com/apps/{cfg.app_slug}/installations/new?{urlencode({'state': state})}"
        return ConnectorStartResponse(
            provider=ConnectorProvider.GITHUB,
            workspace_id=workspace_id,
            authorization_url=f"{self.AUTH_URL}?{query}",
            installation_url=install,
            expires_at=expires,
            notes=["Authorize the GitHub App, then install/select repository access."],
        )

    def complete(self, *, code: str, state: str) -> ConnectorAccountRecord:
        cfg = self._require_config()
        oauth = self.repository.consume_oauth_state(raw_state=state, provider=ConnectorProvider.GITHUB)
        verifier_ref = oauth["verifier_ref"]
        verifier = self.vault.get(verifier_ref)["verifier"]
        payload: dict[str, str] = {
            "client_id": cfg.client_id,
            "code": code,
            "redirect_uri": oauth["redirect_uri"],
            "code_verifier": verifier,
        }
        secret = resolve_env_secret_ref(cfg.client_secret_ref)
        if secret:
            payload["client_secret"] = secret
        response = self.http.post(
            self.TOKEN_URL,
            json=payload,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        token = response.json()
        access_token = token.get("access_token")
        if not access_token:
            raise ConnectorStateError("GITHUB_TOKEN_MISSING", "GitHub token exchange returned no access token")
        self.vault.delete(verifier_ref)
        credential_ref = self.vault.put(token)
        user = self.http.get(
            f"{self.API}/user",
            headers=self._headers(access_token),
        )
        user.raise_for_status()
        identity = user.json()
        return self.repository.upsert_account(
            workspace_id=oauth["workspace_id"],
            provider=ConnectorProvider.GITHUB,
            external_account_id=str(identity["id"]),
            external_account_name=identity.get("login"),
            credential_ref=credential_ref,
            scopes=[],
            metadata={
                "avatar_url": identity.get("avatar_url"),
                "installation_url": f"https://github.com/apps/{cfg.app_slug}/installations/new",
            },
        )

    def refresh_resources(self, *, connector_id: str) -> ConnectorRefreshResponse:
        account = self.repository.get_account(connector_id)
        if account is None:
            raise KeyError(connector_id)
        token = self.vault.get(self.repository.credential_ref(connector_id))["access_token"]
        resources: list[ConnectorResourceRecord] = []
        for installation in self._paginate("/user/installations", token, key="installations"):
            installation_id = installation["id"]
            path = f"/user/installations/{installation_id}/repositories"
            for repo in self._paginate(path, token, key="repositories"):
                resources.append(
                    self.repository.upsert_resource(
                        connector_id=connector_id,
                        resource_type=ConnectorResourceType.GITHUB_REPOSITORY,
                        external_id=str(repo["id"]),
                        name=repo.get("full_name") or repo.get("name") or str(repo["id"]),
                        url=repo.get("html_url"),
                        capabilities=["repository.inspect", "repository.select"],
                        metadata={
                            "installation_id": str(installation_id),
                            "private": bool(repo.get("private")),
                            "default_branch": repo.get("default_branch"),
                        },
                    )
                )
        return ConnectorRefreshResponse(connector=account, resources=resources)

    def _paginate(self, path: str, token: str, *, key: str) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        page = 1
        while page <= 20:
            response = self.http.get(
                f"{self.API}{path}",
                params={"per_page": 100, "page": page},
                headers=self._headers(token),
            )
            response.raise_for_status()
            body = response.json()
            batch = body.get(key, []) if isinstance(body, dict) else []
            output.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return output

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _require_config(self) -> GitHubAppConfig:
        if self.config is None:
            raise ConnectorConfigurationError("GitHub App connector is not configured")
        if not self.vault.available:
            raise ConnectorConfigurationError("connector credential vault is not configured")
        return self.config


class RobloxOAuthConnector:
    OAUTH = "https://apis.roblox.com/oauth"
    CLOUD = "https://apis.roblox.com/cloud/v2"

    def __init__(
        self,
        *,
        config: RobloxOAuthConfig | None,
        repository: PostgresConnectorRepository,
        vault: CredentialVault,
        http: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.vault = vault
        self.http = http or httpx.Client(timeout=20.0, follow_redirects=True)

    @property
    def configured(self) -> bool:
        return self.config is not None and self.vault.available

    def start(self, *, workspace_id: str) -> ConnectorStartResponse:
        cfg = self._require_config()
        state = new_oauth_state()
        verifier = new_pkce_verifier()
        verifier_ref = self.vault.put({"verifier": verifier})
        expires = self.repository.create_oauth_state(
            raw_state=state,
            workspace_id=workspace_id,
            provider=ConnectorProvider.ROBLOX,
            verifier_ref=verifier_ref,
            requested_scopes=cfg.scopes,
            redirect_uri=cfg.redirect_uri,
        )
        query = urlencode(
            {
                "client_id": cfg.client_id,
                "redirect_uri": cfg.redirect_uri,
                "scope": " ".join(cfg.scopes),
                "response_type": "code",
                "state": state,
                "code_challenge": pkce_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        return ConnectorStartResponse(
            provider=ConnectorProvider.ROBLOX,
            workspace_id=workspace_id,
            authorization_url=f"{self.OAUTH}/v1/authorize?{query}",
            expires_at=expires,
            notes=["Roblox Open Cloud OAuth authorizes only the configured scopes."],
        )

    def complete(self, *, code: str, state: str) -> ConnectorAccountRecord:
        cfg = self._require_config()
        oauth = self.repository.consume_oauth_state(raw_state=state, provider=ConnectorProvider.ROBLOX)
        verifier_ref = oauth["verifier_ref"]
        verifier = self.vault.get(verifier_ref)["verifier"]
        form: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": cfg.client_id,
            "code_verifier": verifier,
            "redirect_uri": oauth["redirect_uri"],
        }
        secret = resolve_env_secret_ref(cfg.client_secret_ref)
        if secret:
            form["client_secret"] = secret
        response = self.http.post(f"{self.OAUTH}/v1/token", data=form)
        response.raise_for_status()
        token = response.json()
        access_token = token.get("access_token")
        if not access_token:
            raise ConnectorStateError("ROBLOX_TOKEN_MISSING", "Roblox token exchange returned no access token")
        self.vault.delete(verifier_ref)
        credential_ref = self.vault.put(token)
        identity_response = self.http.get(
            f"{self.OAUTH}/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        identity_response.raise_for_status()
        identity = identity_response.json()
        account_name = identity.get("preferred_username") or identity.get("name") or identity.get("nickname")
        return self.repository.upsert_account(
            workspace_id=oauth["workspace_id"],
            provider=ConnectorProvider.ROBLOX,
            external_account_id=str(identity["sub"]),
            external_account_name=account_name,
            credential_ref=credential_ref,
            scopes=list(oauth["requested_scopes"]),
            metadata={"picture": identity.get("picture")},
        )

    def refresh_resources(self, *, connector_id: str) -> ConnectorRefreshResponse:
        cfg = self._require_config()
        account = self.repository.get_account(connector_id)
        if account is None:
            raise KeyError(connector_id)
        token_data = self.vault.get(self.repository.credential_ref(connector_id))
        access_token = token_data["access_token"]
        secret = resolve_env_secret_ref(cfg.client_secret_ref)
        resources: list[ConnectorResourceRecord] = []
        if secret:
            response = self.http.post(
                f"{self.OAUTH}/v1/token/resources",
                data={
                    "token": access_token,
                    "client_id": cfg.client_id,
                    "client_secret": secret,
                },
            )
            response.raise_for_status()
            body = response.json()
            universe_ids = _extract_universe_ids(body)
            for universe_id in universe_ids:
                detail = self.http.get(
                    f"{self.CLOUD}/universes/{universe_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                metadata: dict[str, Any] = {}
                name = f"Universe {universe_id}"
                if detail.is_success:
                    metadata = detail.json()
                    name = metadata.get("displayName") or metadata.get("name") or name
                resources.append(
                    self.repository.upsert_resource(
                        connector_id=connector_id,
                        resource_type=ConnectorResourceType.ROBLOX_UNIVERSE,
                        external_id=str(universe_id),
                        name=name,
                        capabilities=["roblox.universe.inspect", "roblox.place.register"],
                        metadata=metadata,
                    )
                )
        return ConnectorRefreshResponse(connector=account, resources=resources)

    def add_place(self, *, connector_id: str, universe_id: str, place_id: str) -> ConnectorResourceRecord:
        token = self.vault.get(self.repository.credential_ref(connector_id))["access_token"]
        response = self.http.get(
            f"{self.CLOUD}/universes/{universe_id}/places/{place_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        detail = response.json()
        name = detail.get("displayName") or detail.get("name") or f"Place {place_id}"
        return self.repository.upsert_resource(
            connector_id=connector_id,
            resource_type=ConnectorResourceType.ROBLOX_PLACE,
            external_id=str(place_id),
            name=name,
            capabilities=["roblox.place.inspect", "roblox.place.select"],
            metadata={"universe_id": str(universe_id), **detail},
        )

    def _require_config(self) -> RobloxOAuthConfig:
        if self.config is None:
            raise ConnectorConfigurationError("Roblox OAuth connector is not configured")
        if not self.vault.available:
            raise ConnectorConfigurationError("connector credential vault is not configured")
        return self.config


def _extract_universe_ids(body: Any) -> list[str]:
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in {"universe", "universeid", "universe_id"} and isinstance(item, (str, int)):
                    found.append(str(item))
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(body)
    return list(dict.fromkeys(found))
