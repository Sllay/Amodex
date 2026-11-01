"""Web-first workspace product contracts.

Workspace is organizational/conversational state. Environment remains the execution
boundary and this module deliberately grants no repository or runner authority.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from amodex.contracts.ids import UUIDv7Str


class WorkspaceStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class ChatStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class ChatRole(StrEnum):
    OWNER = "OWNER"
    AMODEX = "AMODEX"
    SYSTEM = "SYSTEM"


class WorkspaceMemoryStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ExecutionDraftStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)


class WorkspaceRecord(BaseModel):
    workspace_id: UUIDv7Str
    name: str
    description: str | None = None
    status: WorkspaceStatus
    created_at: datetime
    updated_at: datetime


class WorkspaceEnvironmentBindRequest(BaseModel):
    environment_id: UUIDv7Str
    label: str | None = Field(default=None, max_length=160)


class WorkspaceEnvironmentRecord(BaseModel):
    workspace_id: UUIDv7Str
    environment_id: UUIDv7Str
    label: str | None = None
    created_at: datetime


class ChatCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    active_environment_id: UUIDv7Str | None = None


class ChatRecord(BaseModel):
    chat_id: UUIDv7Str
    workspace_id: UUIDv7Str
    title: str
    status: ChatStatus
    active_environment_id: UUIDv7Str | None = None
    rolling_summary: str | None = None
    created_at: datetime
    updated_at: datetime


class ChatMessageCreateRequest(BaseModel):
    role: ChatRole = ChatRole.OWNER
    content: str = Field(min_length=1, max_length=200000)


class ChatMessageRecord(BaseModel):
    message_id: UUIDv7Str
    chat_id: UUIDv7Str
    role: ChatRole
    content: str
    created_at: datetime


class WorkspaceMemoryCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=100000)
    source_ref: str | None = Field(default=None, max_length=1000)


class WorkspaceMemoryRecord(BaseModel):
    memory_id: UUIDv7Str
    workspace_id: UUIDv7Str
    content: str
    source_ref: str | None = None
    status: WorkspaceMemoryStatus
    created_at: datetime
    updated_at: datetime


class WorkspaceMemoryDecisionRequest(BaseModel):
    status: WorkspaceMemoryStatus


class AttachmentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=160)
    size_bytes: int = Field(ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    storage_ref: str = Field(min_length=1, max_length=1000)


class AttachmentRecord(BaseModel):
    attachment_id: UUIDv7Str
    chat_id: UUIDv7Str
    name: str
    media_type: str
    size_bytes: int
    sha256: str | None = None
    storage_ref: str
    created_at: datetime


class ExecutionDraftCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=100000)
    instructions: str = Field(min_length=1, max_length=200000)
    validation: str | None = Field(default=None, max_length=100000)
    environment_id: UUIDv7Str | None = None


class ExecutionDraftRecord(BaseModel):
    draft_id: UUIDv7Str
    chat_id: UUIDv7Str
    environment_id: UUIDv7Str | None = None
    title: str
    objective: str
    instructions: str
    validation: str | None = None
    status: ExecutionDraftStatus
    run_id: UUIDv7Str | None = None
    created_at: datetime
    updated_at: datetime


class ExecutionDraftStateRequest(BaseModel):
    status: ExecutionDraftStatus
