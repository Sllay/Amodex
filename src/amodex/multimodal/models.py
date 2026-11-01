"""Contracts for explicit Step 3 multimodal actions.

These records describe user-triggered media/search operations only. They do not
promote a model, select a Stage 5 route, or grant execution authority.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from amodex.contracts.ids import UUIDv7Str
from amodex.workspace.models import AttachmentRecord


class MultimodalOperationKind(StrEnum):
    VISION = "VISION"
    STT = "STT"
    TTS = "TTS"
    WEB_RESEARCH = "WEB_RESEARCH"


class MultimodalOperationStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class VisionRequest(BaseModel):
    prompt: str = Field(
        default="Descreva e analise esta imagem de forma objetiva, destacando o que for relevante para o projeto.",
        min_length=1,
        max_length=100000,
    )


class TranscriptionRequest(BaseModel):
    language: str | None = Field(default=None, min_length=2, max_length=16)


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    voice: str | None = Field(default=None, min_length=1, max_length=120)


class ResearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    max_results: int = Field(default=5, ge=1, le=10)
    language: str | None = Field(default=None, min_length=2, max_length=32)


class ResearchSource(BaseModel):
    title: str = Field(min_length=1, max_length=1000)
    url: str = Field(min_length=1, max_length=4000)
    snippet: str = Field(default="", max_length=8000)
    engine: str | None = Field(default=None, max_length=160)


class MessageAttachmentLinkRequest(BaseModel):
    attachment_ids: list[UUIDv7Str] = Field(min_length=1, max_length=32)


class MessageAttachmentRecord(BaseModel):
    message_id: UUIDv7Str
    chat_id: UUIDv7Str
    attachment: AttachmentRecord
    created_at: datetime


class MultimodalOperationRecord(BaseModel):
    operation_id: UUIDv7Str
    chat_id: UUIDv7Str
    kind: MultimodalOperationKind
    status: MultimodalOperationStatus
    attachment_id: UUIDv7Str | None = None
    output_attachment_id: UUIDv7Str | None = None
    provider: str | None = None
    model: str | None = None
    billing_class: str | None = None
    input_text: str | None = None
    result_text: str | None = None
    error_code: str | None = None
    provenance: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
