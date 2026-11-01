"""Durable connector accounts/resources and single-use OAuth state storage."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from amodex.connectors.models import (
    ConnectorAccountRecord,
    ConnectorProvider,
    ConnectorResourceRecord,
    ConnectorResourceType,
    ConnectorState,
)
from amodex.connectors.oauth import hash_oauth_state
from amodex.contracts.ids import new_uuid7


class ConnectorStateError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class PostgresConnectorRepository:
    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    @property
    def dsn(self) -> str:
        return self._dsn

    def schema_ready(self) -> bool:
        with psycopg.connect(self._dsn) as conn:
            row = conn.execute(
                """
                SELECT to_regclass('public.amodex_connector_accounts'),
                       to_regclass('public.amodex_connector_resources'),
                       to_regclass('public.amodex_connector_oauth_states')
                """
            ).fetchone()
        return bool(row and all(row))

    def create_oauth_state(
        self,
        *,
        raw_state: str,
        workspace_id: str,
        provider: ConnectorProvider,
        verifier_ref: str,
        requested_scopes: tuple[str, ...],
        redirect_uri: str,
        ttl_seconds: int = 600,
    ) -> datetime:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        with psycopg.connect(self._dsn) as conn:
            workspace = conn.execute(
                "SELECT 1 FROM amodex_workspace_projects WHERE workspace_id=%s AND status='ACTIVE'",
                (workspace_id,),
            ).fetchone()
            if workspace is None:
                raise KeyError(workspace_id)
            conn.execute(
                """
                INSERT INTO amodex_connector_oauth_states(
                    state_hash,workspace_id,provider,verifier_ref,requested_scopes,redirect_uri,
                    created_at,expires_at,used_at
                ) VALUES (%s,%s,%s,%s,%s::jsonb,%s,NOW(),%s,NULL)
                """,
                (
                    hash_oauth_state(raw_state),
                    workspace_id,
                    provider.value,
                    verifier_ref,
                    json.dumps(list(requested_scopes)),
                    redirect_uri,
                    expires_at,
                ),
            )
        return expires_at

    def consume_oauth_state(self, *, raw_state: str, provider: ConnectorProvider) -> dict[str, Any]:
        digest = hash_oauth_state(raw_state)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                row = conn.execute(
                    "SELECT * FROM amodex_connector_oauth_states WHERE state_hash=%s FOR UPDATE",
                    (digest,),
                ).fetchone()
                if row is None:
                    raise ConnectorStateError("OAUTH_STATE_UNKNOWN", "OAuth state is not recognized")
                if row["provider"] != provider.value:
                    raise ConnectorStateError("OAUTH_PROVIDER_MISMATCH", "OAuth state belongs to another provider")
                if row["used_at"] is not None:
                    raise ConnectorStateError("OAUTH_STATE_ALREADY_USED", "OAuth state was already consumed")
                if row["expires_at"] <= datetime.now(timezone.utc):
                    raise ConnectorStateError("OAUTH_STATE_EXPIRED", "OAuth state expired")
                conn.execute(
                    "UPDATE amodex_connector_oauth_states SET used_at=NOW() WHERE state_hash=%s",
                    (digest,),
                )
        return {
            "workspace_id": str(row["workspace_id"]),
            "provider": row["provider"],
            "verifier_ref": row["verifier_ref"],
            "requested_scopes": list(row["requested_scopes"] or []),
            "redirect_uri": row["redirect_uri"],
        }

    def upsert_account(
        self,
        *,
        workspace_id: str,
        provider: ConnectorProvider,
        external_account_id: str,
        external_account_name: str | None,
        credential_ref: str,
        scopes: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> ConnectorAccountRecord:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                SELECT * FROM amodex_connector_accounts
                WHERE workspace_id=%s AND provider=%s AND external_account_id=%s AND state<>'REVOKED'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (workspace_id, provider.value, external_account_id),
            ).fetchone()
            if row is None:
                connector_id = new_uuid7()
                row = conn.execute(
                    """
                    INSERT INTO amodex_connector_accounts(
                        connector_id,workspace_id,provider,external_account_id,external_account_name,
                        credential_ref,state,scopes,metadata,created_at,updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,'CONNECTED',%s::jsonb,%s::jsonb,NOW(),NOW()) RETURNING *
                    """,
                    (
                        connector_id, workspace_id, provider.value, external_account_id,
                        external_account_name, credential_ref, json.dumps(scopes), json.dumps(metadata or {}),
                    ),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    UPDATE amodex_connector_accounts
                    SET external_account_name=%s,credential_ref=%s,state='CONNECTED',scopes=%s::jsonb,
                        metadata=%s::jsonb,updated_at=NOW()
                    WHERE connector_id=%s RETURNING *
                    """,
                    (
                        external_account_name, credential_ref, json.dumps(scopes),
                        json.dumps(metadata or {}), row["connector_id"],
                    ),
                ).fetchone()
        assert row is not None
        return _account(row)

    def get_account(self, connector_id: str) -> ConnectorAccountRecord | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT * FROM amodex_connector_accounts WHERE connector_id=%s", (connector_id,)
            ).fetchone()
        return _account(row) if row else None

    def list_accounts(self, *, workspace_id: str) -> tuple[ConnectorAccountRecord, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                """
                SELECT * FROM amodex_connector_accounts
                WHERE workspace_id=%s ORDER BY updated_at DESC
                """,
                (workspace_id,),
            ).fetchall()
        return tuple(_account(row) for row in rows)

    def credential_ref(self, connector_id: str) -> str:
        account = self.get_account(connector_id)
        if account is None:
            raise KeyError(connector_id)
        if account.state != ConnectorState.CONNECTED or not account.credential_ref:
            raise ConnectorStateError("CONNECTOR_NOT_CONNECTED", connector_id)
        return account.credential_ref

    def upsert_resource(
        self,
        *,
        connector_id: str,
        resource_type: ConnectorResourceType,
        external_id: str,
        name: str,
        url: str | None = None,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ConnectorResourceRecord:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            account = conn.execute(
                "SELECT state FROM amodex_connector_accounts WHERE connector_id=%s",
                (connector_id,),
            ).fetchone()
            if account is None:
                raise KeyError(connector_id)
            if account["state"] != ConnectorState.CONNECTED.value:
                raise ConnectorStateError("CONNECTOR_NOT_CONNECTED", connector_id)
            resource_id = new_uuid7()
            row = conn.execute(
                """
                INSERT INTO amodex_connector_resources(
                    resource_id,connector_id,resource_type,external_id,name,url,capabilities,metadata,
                    created_at,updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,NOW(),NOW())
                ON CONFLICT (connector_id,resource_type,external_id) DO UPDATE SET
                    name=EXCLUDED.name,url=EXCLUDED.url,capabilities=EXCLUDED.capabilities,
                    metadata=EXCLUDED.metadata,updated_at=NOW()
                RETURNING *
                """,
                (
                    resource_id, connector_id, resource_type.value, external_id, name, url,
                    json.dumps(capabilities or []), json.dumps(metadata or {}),
                ),
            ).fetchone()
        assert row is not None
        return _resource(row)

    def list_resources(self, *, connector_id: str) -> tuple[ConnectorResourceRecord, ...]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                """
                SELECT * FROM amodex_connector_resources
                WHERE connector_id=%s ORDER BY resource_type,name
                """,
                (connector_id,),
            ).fetchall()
        return tuple(_resource(row) for row in rows)

    def get_resource(self, resource_id: str) -> ConnectorResourceRecord | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT * FROM amodex_connector_resources WHERE resource_id=%s", (resource_id,)
            ).fetchone()
        return _resource(row) if row else None

    def bind_resource_environment(self, *, resource_id: str, environment_id: str | None) -> ConnectorResourceRecord:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                resource = conn.execute(
                    """
                    SELECT r.*,a.workspace_id FROM amodex_connector_resources r
                    JOIN amodex_connector_accounts a ON a.connector_id=r.connector_id
                    WHERE r.resource_id=%s FOR UPDATE OF r
                    """,
                    (resource_id,),
                ).fetchone()
                if resource is None:
                    raise KeyError(resource_id)
                if environment_id is not None:
                    bound = conn.execute(
                        """
                        SELECT 1 FROM amodex_workspace_environments
                        WHERE workspace_id=%s AND environment_id=%s
                        """,
                        (resource["workspace_id"], environment_id),
                    ).fetchone()
                    if bound is None:
                        raise ConnectorStateError(
                            "RESOURCE_ENVIRONMENT_NOT_IN_WORKSPACE",
                            "resource may only bind to an Environment in the same Workspace",
                        )
                row = conn.execute(
                    """
                    UPDATE amodex_connector_resources SET bound_environment_id=%s,updated_at=NOW()
                    WHERE resource_id=%s RETURNING *
                    """,
                    (environment_id, resource_id),
                ).fetchone()
        assert row is not None
        return _resource(row)

    def revoke(self, connector_id: str) -> str | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                UPDATE amodex_connector_accounts SET state='REVOKED',updated_at=NOW()
                WHERE connector_id=%s RETURNING credential_ref
                """,
                (connector_id,),
            ).fetchone()
        if row is None:
            raise KeyError(connector_id)
        return row["credential_ref"]


def _account(row: dict[str, Any]) -> ConnectorAccountRecord:
    return ConnectorAccountRecord(
        connector_id=str(row["connector_id"]), workspace_id=str(row["workspace_id"]),
        provider=ConnectorProvider(row["provider"]), external_account_id=row["external_account_id"],
        external_account_name=row["external_account_name"], credential_ref=row["credential_ref"],
        state=ConnectorState(row["state"]), scopes=list(row["scopes"] or []),
        metadata=dict(row["metadata"] or {}), created_at=row["created_at"], updated_at=row["updated_at"]
    )


def _resource(row: dict[str, Any]) -> ConnectorResourceRecord:
    return ConnectorResourceRecord(
        resource_id=str(row["resource_id"]), connector_id=str(row["connector_id"]),
        resource_type=ConnectorResourceType(row["resource_type"]), external_id=row["external_id"],
        name=row["name"], url=row["url"], capabilities=list(row["capabilities"] or []),
        metadata=dict(row["metadata"] or {}),
        bound_environment_id=str(row["bound_environment_id"]) if row["bound_environment_id"] else None,
        created_at=row["created_at"], updated_at=row["updated_at"]
    )
