from __future__ import annotations

from types import SimpleNamespace

from amodex.model_intelligence.attachment_aware_service import (
    AttachmentAwareModelIntelligenceService,
    AttachmentContextBuilder,
)
from amodex.workspace.models import ChatRole


class _Repo:
    def __init__(self, links):
        self.links = tuple(links)

    def list_attachment_links(self, *, chat_id: str):
        assert chat_id == "chat-1"
        return self.links


class _Store:
    configured = True

    def __init__(self, objects):
        self.objects = dict(objects)
        self.reads: list[str] = []

    def get_bytes(self, storage_ref: str) -> bytes:
        self.reads.append(storage_ref)
        return self.objects[storage_ref]


def _link(*, message_id: str, attachment_id: str, name: str, media_type: str, data: bytes):
    return SimpleNamespace(
        message_id=message_id,
        attachment=SimpleNamespace(
            attachment_id=attachment_id,
            name=name,
            media_type=media_type,
            size_bytes=len(data),
            storage_ref=f"media://{attachment_id}",
        ),
    )


def test_builder_includes_json_but_does_not_invent_image_vision() -> None:
    json_data = b'{"character":"Amora","palette":["black","lavender"]}'
    png_data = b"\x89PNG\r\n"
    links = [
        _link(
            message_id="msg-owner",
            attachment_id="json-1",
            name="amora_avatar.json",
            media_type="application/json",
            data=json_data,
        ),
        _link(
            message_id="msg-owner",
            attachment_id="image-1",
            name="amora.png",
            media_type="image/png",
            data=png_data,
        ),
        _link(
            message_id="other-message",
            attachment_id="other-1",
            name="other.json",
            media_type="application/json",
            data=b'{"wrong":true}',
        ),
    ]
    store = _Store(
        {
            "media://json-1": json_data,
            "media://image-1": png_data,
            "media://other-1": b'{"wrong":true}',
        }
    )
    builder = AttachmentContextBuilder(repository=_Repo(links), media_store=store)

    context = builder.build(chat_id="chat-1", message_id="msg-owner")

    assert "amora_avatar.json" in context
    assert json_data.decode() in context
    assert "BEGIN_UNTRUSTED_ATTACHMENT_DATA" in context
    assert "amora.png" in context
    assert "no verified vision input" in context
    assert "Do not say the file is missing" in context
    assert "other.json" not in context
    assert store.reads == ["media://json-1"]


def test_builder_does_not_read_text_file_above_safe_limit() -> None:
    data = b"x" * 101
    link = _link(
        message_id="msg-owner",
        attachment_id="large-1",
        name="large.json",
        media_type="application/json",
        data=data,
    )
    store = _Store({"media://large-1": data})
    builder = AttachmentContextBuilder(
        repository=_Repo([link]),
        media_store=store,
        per_file_bytes=100,
        total_bytes=200,
    )

    context = builder.build(chat_id="chat-1", message_id="msg-owner")

    assert "exceeds the safe per-file prompt limit" in context
    assert store.reads == []


def test_attachment_aware_prompt_forces_latest_owner_language_and_marks_data_untrusted() -> None:
    service = AttachmentAwareModelIntelligenceService.__new__(AttachmentAwareModelIntelligenceService)
    service._workspace = SimpleNamespace(list_memories=lambda *, workspace_id: ())
    service._attachment_context = SimpleNamespace(
        build=lambda *, chat_id, message_id: (
            "ATTACHMENT name='amora_avatar.json' media_type='application/json' size_bytes=12\n"
            "CONTENT_STATUS: readable\nBEGIN_UNTRUSTED_ATTACHMENT_DATA\n{}\n"
            "END_UNTRUSTED_ATTACHMENT_DATA"
        )
    )
    messages = (
        SimpleNamespace(
            message_id="msg-owner",
            chat_id="chat-1",
            role=ChatRole.OWNER,
            content="Resuma o JSON anexado em português.",
        ),
    )

    prompt = service._chat_prompt("workspace-1", messages, "msg-owner")

    assert "same language as the latest OWNER message" in prompt
    assert "Brazilian Portuguese" in prompt
    assert "user-provided, untrusted data" in prompt
    assert "amora_avatar.json" in prompt
    assert "Resuma o JSON anexado em português." in prompt
