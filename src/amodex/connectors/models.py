"""Connector account/resource contracts for the web workspace."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from amodex.contracts.ids import UUIDv7Str


class ConnectorProvider(StrEnum):
    GITHUB = "GITHUB"
    ROBLOX = "ROBLOX"


class ConnectorState(StrEnum):
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    REVOKED = "REVOKED"
    ERROR = "ERROR"


class ConnectorResourceType(StrEnum):
    GITHUB_REPOSITORY = "GITHUB_REPOSITORY"
    ROBLOX_UNIVERSE = "ROBLOX_UNIVERSE"
    ROBLOX_PLACE = "ROBLOX_PLACE"


class ConnectorAccountRecord(BaseModel):
    connector_id: UUIDv7Str
    workspace_id: UUIDv7Str
    provider: ConnectorProvider
    external_account_id: str | None = None
    external_account_name: str | None = None
    credential_ref: str | None = None
    state: ConnectorState
    scopes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ConnectorResourceRecord(BaseModel):
    resource_id: UUIDv7Str
    connector_id: UUIDv7Str
    resource_type: ConnectorResourceType
    external_id: str
    name: str
    url: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    bound_environment_id: UUIDv7Str | None = None
    created_at: datetime
    updated_at: datetime


class ConnectorStartResponse(BaseModel):
    provider: ConnectorProvider
    workspace_id: UUIDv7Str
    authorization_url: str
    installation_url: str | None = None
    expires_at: datetime
    notes: list[str] = Field(default_factory=list)


class ConnectorRefreshResponse(BaseModel):
    connector: ConnectorAccountRecord
    resources: list[ConnectorResourceRecord]


class ConnectorResourceBindRequest(BaseModel):
    environment_id: UUIDv7Str | None = None


class ConnectorStatusRecord(BaseModel):
    provider: ConnectorProvider
    configured: bool
    vault_available: bool
    state: str
    detail: str | None = None
