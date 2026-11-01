from __future__ import annotations

import os
from pathlib import Path
import stat

import psycopg
import pytest
from fastapi.testclient import TestClient

from amodex.api.app import create_app
from amodex.api.workspace_app import create_workspace_app
from amodex.multimodal.clients import (
    AudioCapabilityResult,
    MultimodalClients,
    ResearchCapabilityResult,
    TextCapabilityResult,
)
from amodex.multimodal.models import ResearchSource
from amodex.multimodal.object_store import FileMediaStore, MediaStoreError, MemoryMediaStore
from amodex.storage.environment import PostgresEnvironmentRepository
from amodex.storage.environment_product import PostgresEnvironmentProductRepository
from amodex.storage.multimodal import PostgresMultimodalRepository
from amodex.storage.postgres import PostgresRunRepository
from amodex.storage.product import PostgresProductRepository
from amodex.storage.routing import PostgresRoutingRepository
from amodex.storage.workspace import PostgresWorkspaceRepository


class FakeVision:
    ready = True
    readiness_reason = "READY"
    provider = "fixture-vision"
    model = "fixture-v1"
    billing_class = "free"

    async def analyze(self, *, data: bytes, media_type: str, prompt: str) -> TextCapabilityResult:
        assert data.startswith(b"image:")
        assert media_type == "image/png"
        assert prompt
        return TextCapabilityResult(
            text="A imagem mostra uma ilha de teste.",
            provider=self.provider,
            model=self.model,
            billing_class=self.billing_class,
            provenance={"fixture": "vision"},
        )


class FakeStt:
    ready = True
    readiness_reason = "READY"
    provider = "fixture-stt"
    model = "fixture-stt-v1"
    billing_class = "free"

    async def transcribe(self, *, data: bytes, name: str, media_type: str, language: str | None) -> TextCapabilityResult:
        assert data.startswith(b"audio:")
        assert media_type.startswith("audio/")
        assert name
        return TextCapabilityResult(
            text="crie uma ponte perto do rio",
            provider=self.provider,
            model=self.model,
            billing_class=self.billing_class,
            provenance={"fixture": "stt", "language": language},
        )


class FakeTts:
    ready = True
    readiness_reason = "READY"
    provider = "fixture-tts"
    model = "fixture-tts-v1"
    billing_class = "free"

    async def synthesize(self, *, text: str, voice: str | None) -> AudioCapabilityResult:
        assert text
        return AudioCapabilityResult(
            data=b"fake-mp3-audio",
            media_type="audio/mpeg",
            extension="mp3",
            provider=self.provider,
            model=self.model,
            billing_class=self.billing_class,
            provenance={"fixture": "tts", "voice": voice or "default"},
        )


class FakeResearch:
    ready = True
    readiness_reason = "READY"
    provider = "fixture-searxng"

    async def search(self, *, query: str, max_results: int, language: str | None) -> ResearchCapabilityResult:
        assert query
        assert max_results >= 1
        return ResearchCapabilityResult(
            sources=(
                ResearchSource(
                    title="Roblox Creator Hub",
                    url="https://create.roblox.com/docs",
                    snippet="Documentação oficial para criação.",
                    engine="fixture",
                ),
            ),
            provider=self.provider,
            provenance={"fixture": "research", "page_fetch": False, "language": language},
        )


class BlockedVision:
    ready = False
    readiness_reason = "VISION_BILLING_NOT_FREE"
    provider = "fixture-paid"
    model = "fixture-paid-v1"
    billing_class = "paid"

    async def analyze(self, **_: object) -> TextCapabilityResult:  # pragma: no cover - must never run
        raise AssertionError("blocked provider must never be called")


def _clients(*, vision=None) -> MultimodalClients:
    return MultimodalClients(
        vision=vision or FakeVision(),
        stt=FakeStt(),
        tts=FakeTts(),
        research=FakeResearch(),
    )


@pytest.fixture()
def multimodal_state() -> tuple[
    PostgresRunRepository,
    PostgresProductRepository,
    PostgresEnvironmentRepository,
    PostgresEnvironmentProductRepository,
    PostgresWorkspaceRepository,
    PostgresMultimodalRepository,
]:
    dsn = os.environ["AMODEX_TEST_DATABASE_URL"]
    runs = PostgresRunRepository(dsn)
    runs.migrate_all(Path("migrations"))
    with psycopg.connect(dsn) as conn:
        conn.execute(Path("migrations/stage4/0001_promotion.sql").read_text(encoding="utf-8"))
    PostgresRoutingRepository(dsn).migrate(Path("migrations/stage5/0001_routing_measurements.sql"))
    product = PostgresProductRepository(dsn)
    product.migrate(Path("migrations/stage6/0001_product_surfaces.sql"))
    environments = PostgresEnvironmentRepository(dsn)
    environments.migrate(Path("migrations/postbaseline/0001_environment_isolation_budget.sql"))
    environment_product = PostgresEnvironmentProductRepository(dsn)
    workspace = PostgresWorkspaceRepository(dsn)
    multimodal = PostgresMultimodalRepository(dsn)
    with psycopg.connect(dsn) as conn:
        conn.execute(Path("migrations/postbaseline/0002_environment_product_runners.sql").read_text(encoding="utf-8"))
        conn.execute(Path("migrations/postbaseline/0003_web_workspace.sql").read_text(encoding="utf-8"))
        conn.execute(Path("migrations/postbaseline/0004_connectors.sql").read_text(encoding="utf-8"))
        conn.execute(Path("migrations/postbaseline/0005_multimodal_chat.sql").read_text(encoding="utf-8"))
        conn.execute(
            """
            TRUNCATE
                amodex_workspace_projects,
                amodex_environments,
                amodex_projects
            CASCADE
            """
        )
    assert workspace.schema_ready()
    assert multimodal.schema_ready()
    return runs, product, environments, environment_product, workspace, multimodal


def _client(
    multimodal_state,
    *,
    store: MemoryMediaStore | None = None,
    clients: MultimodalClients | None = None,
    max_upload_bytes: int = 64,
) -> tuple[TestClient, MemoryMediaStore]:
    runs, product, environments, environment_product, workspace, multimodal = multimodal_state
    base = create_app(
        runs,
        product_repository=product,
        environment_repository=environments,
        environment_product_repository=environment_product,
    )
    media = store or MemoryMediaStore()
    app = create_workspace_app(
        base_app=base,
        workspace_repository=workspace,
        multimodal_repository=multimodal,
        media_store=media,
        multimodal_clients=clients or _clients(),
        max_upload_bytes=max_upload_bytes,
    )
    return TestClient(app), media


def _make_chat(client: TestClient, *, name: str = "Game") -> tuple[dict, dict]:
    workspace = client.post("/v1/workspaces", json={"name": name}).json()
    chat = client.post(
        f"/v1/workspaces/{workspace['workspace_id']}/chats",
        json={"title": "Multimodal"},
    ).json()
    return workspace, chat


def _upload(client: TestClient, chat_id: str, data: bytes, name: str, media_type: str) -> dict:
    response = client.post(
        f"/v1/chats/{chat_id}/uploads",
        content=data,
        headers={"content-type": media_type, "x-file-name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_step3_status_and_mobile_workspace_controls(multimodal_state) -> None:
    client, _ = _client(multimodal_state)
    with client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Amodex Workspace" in page.text
        assert "AI gated" in page.text
        assert 'id="mmFileInput"' in page.text
        assert 'id="mmMic"' in page.text
        assert "mmResearch()" in page.text

        status = client.get("/v1/multimodal/status")
        assert status.status_code == 200
        payload = status.json()
        assert payload["binary_upload"] == "READY"
        assert payload["vision"]["billing_class"] == "free"
        assert payload["stt"]["ready"] is True
        assert payload["browser_tts"] == "AVAILABLE"
        assert payload["web_research"]["ready"] is True
        assert payload["automatic_model_routing"] == "GATED_STEP_4"
        assert payload["execution_authority"] == "ENVIRONMENT_ONLY"


def test_binary_upload_content_and_message_attachment_fencing(multimodal_state) -> None:
    client, _ = _client(multimodal_state)
    with client:
        _, chat_a = _make_chat(client, name="A")
        _, chat_b = _make_chat(client, name="B")
        payload = b"image:binary-data"
        attachment = _upload(client, chat_a["chat_id"], payload, "map.png", "image/png")
        assert attachment["sha256"]
        assert attachment["storage_ref"].startswith("media://sha256/")
        assert payload.decode() not in attachment["storage_ref"]

        content = client.get(f"/v1/attachments/{attachment['attachment_id']}/content")
        assert content.status_code == 200
        assert content.content == payload
        assert content.headers["content-type"].startswith("image/png")

        message_a = client.post(
            f"/v1/chats/{chat_a['chat_id']}/messages",
            json={"role": "OWNER", "content": "Analise o mapa"},
        ).json()
        linked = client.post(
            f"/v1/messages/{message_a['message_id']}/attachments",
            json={"attachment_ids": [attachment["attachment_id"]]},
        )
        assert linked.status_code == 201
        links = client.get(f"/v1/chats/{chat_a['chat_id']}/attachment-links").json()
        assert links[0]["attachment"]["attachment_id"] == attachment["attachment_id"]

        message_b = client.post(
            f"/v1/chats/{chat_b['chat_id']}/messages",
            json={"role": "OWNER", "content": "cross"},
        ).json()
        crossed = client.post(
            f"/v1/messages/{message_b['message_id']}/attachments",
            json={"attachment_ids": [attachment["attachment_id"]]},
        )
        assert crossed.status_code == 409
        assert crossed.json()["detail"]["code"] == "ATTACHMENT_MESSAGE_CHAT_MISMATCH"

        dsn = os.environ["AMODEX_TEST_DATABASE_URL"]
        with psycopg.connect(dsn) as conn:
            bytea_columns = conn.execute(
                """
                SELECT count(*) FROM information_schema.columns
                WHERE table_schema='public'
                  AND table_name IN ('amodex_workspace_attachments','amodex_multimodal_operations')
                  AND data_type='bytea'
                """
            ).fetchone()[0]
        assert bytea_columns == 0


def test_upload_size_limit_fails_before_persisting(multimodal_state) -> None:
    client, _ = _client(multimodal_state, max_upload_bytes=8)
    with client:
        _, chat = _make_chat(client)
        response = client.post(
            f"/v1/chats/{chat['chat_id']}/uploads",
            content=b"123456789",
            headers={"content-type": "application/octet-stream", "x-file-name": "too-big.bin"},
        )
        assert response.status_code == 413
        assert response.json()["detail"]["code"] == "UPLOAD_TOO_LARGE"


def test_explicit_image_analysis_creates_audited_internal_message(multimodal_state) -> None:
    client, _ = _client(multimodal_state)
    with client:
        _, chat = _make_chat(client)
        attachment = _upload(client, chat["chat_id"], b"image:map", "map.png", "image/png")
        response = client.post(
            f"/v1/attachments/{attachment['attachment_id']}/vision",
            json={"prompt": "O que há nesta imagem?"},
        )
        assert response.status_code == 200, response.text
        op = response.json()
        assert op["kind"] == "VISION"
        assert op["status"] == "SUCCEEDED"
        assert op["billing_class"] == "free"
        assert "ilha" in op["result_text"]

        messages = client.get(f"/v1/chats/{chat['chat_id']}/messages").json()
        assert messages[-1]["role"] == "AMODEX"
        assert "Análise visual" in messages[-1]["content"]

        impersonation = client.post(
            f"/v1/chats/{chat['chat_id']}/messages",
            json={"role": "AMODEX", "content": "fake"},
        )
        assert impersonation.status_code == 403


def test_audio_transcription_returns_text_without_auto_sending(multimodal_state) -> None:
    client, _ = _client(multimodal_state)
    with client:
        _, chat = _make_chat(client)
        attachment = _upload(client, chat["chat_id"], b"audio:voice", "voice.webm", "audio/webm")
        before = client.get(f"/v1/chats/{chat['chat_id']}/messages").json()
        response = client.post(
            f"/v1/attachments/{attachment['attachment_id']}/transcribe",
            json={"language": "pt"},
        )
        assert response.status_code == 200, response.text
        op = response.json()
        assert op["kind"] == "STT"
        assert op["status"] == "SUCCEEDED"
        assert op["result_text"] == "crie uma ponte perto do rio"
        after = client.get(f"/v1/chats/{chat['chat_id']}/messages").json()
        assert after == before


def test_server_tts_creates_content_addressed_audio_output(multimodal_state) -> None:
    client, _ = _client(multimodal_state)
    with client:
        _, chat = _make_chat(client)
        response = client.post(
            f"/v1/chats/{chat['chat_id']}/speech",
            json={"text": "Teste de voz", "voice": None},
        )
        assert response.status_code == 200, response.text
        op = response.json()
        assert op["kind"] == "TTS"
        assert op["status"] == "SUCCEEDED"
        assert op["output_attachment_id"]
        audio = client.get(f"/v1/attachments/{op['output_attachment_id']}/content")
        assert audio.status_code == 200
        assert audio.content == b"fake-mp3-audio"
        assert audio.headers["content-type"].startswith("audio/mpeg")


def test_web_research_uses_search_snippets_and_records_sources(multimodal_state) -> None:
    client, _ = _client(multimodal_state)
    with client:
        _, chat = _make_chat(client)
        response = client.post(
            f"/v1/chats/{chat['chat_id']}/research",
            json={"query": "Roblox Studio docs", "max_results": 5, "language": "pt-BR"},
        )
        assert response.status_code == 200, response.text
        op = response.json()
        assert op["kind"] == "WEB_RESEARCH"
        assert op["status"] == "SUCCEEDED"
        assert op["provenance"]["page_fetch"] is False
        assert op["provenance"]["sources"][0]["url"] == "https://create.roblox.com/docs"

        messages = client.get(f"/v1/chats/{chat['chat_id']}/messages").json()
        assert messages[-1]["role"] == "AMODEX"
        assert "https://create.roblox.com/docs" in messages[-1]["content"]


def test_non_free_model_backed_capability_is_blocked_without_fallback(multimodal_state) -> None:
    client, _ = _client(multimodal_state, clients=_clients(vision=BlockedVision()))
    with client:
        _, chat = _make_chat(client)
        attachment = _upload(client, chat["chat_id"], b"image:map", "map.png", "image/png")
        response = client.post(
            f"/v1/attachments/{attachment['attachment_id']}/vision",
            json={"prompt": "analise"},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "VISION_BILLING_NOT_FREE"
        operations = client.get(f"/v1/chats/{chat['chat_id']}/multimodal-operations").json()
        assert operations[0]["status"] == "BLOCKED"
        assert operations[0]["billing_class"] == "paid"


def test_file_media_store_is_private_content_addressed_and_traversal_safe(tmp_path) -> None:
    root = tmp_path / "media"
    store = FileMediaStore(root)
    saved = store.put_bytes(b"hello")
    assert saved.storage_ref.startswith("media://sha256/")
    assert store.get_bytes(saved.storage_ref) == b"hello"
    assert stat.S_IMODE(root.stat().st_mode) == 0o700

    digest = saved.sha256
    object_path = root / "sha256" / digest[:2] / digest
    assert stat.S_IMODE(object_path.stat().st_mode) == 0o600
    with pytest.raises(MediaStoreError):
        store.get_bytes("media://sha256/../../etc/passwd")
