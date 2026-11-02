from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from amodex.api.global_chat_routes import GLOBAL_WORKSPACE_SENTINEL, build_global_chat_router
from amodex.api.theme_web import ThemeConfig, augment_theme_html, theme_css, tone
from amodex.api.workspace_v2 import augment_workspace_v2_html
from amodex.api.workspace_web import WORKSPACE_HTML
from amodex.contracts.ids import new_uuid7
from amodex.workspace.models import ChatRecord, ChatStatus, WorkspaceRecord, WorkspaceStatus


class _FakeWorkspaceRepository:
    def __init__(self) -> None:
        self.workspaces: list[WorkspaceRecord] = []
        self.chats: list[ChatRecord] = []

    def schema_ready(self) -> bool:
        return True

    def list_workspaces(self, *, limit: int = 200):
        return tuple(self.workspaces[:limit])

    def create_workspace(self, *, name: str, description: str | None = None) -> WorkspaceRecord:
        now = datetime.now(UTC)
        item = WorkspaceRecord(
            workspace_id=new_uuid7(),
            name=name,
            description=description,
            status=WorkspaceStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        self.workspaces.insert(0, item)
        return item

    def list_chats(self, *, workspace_id: str, limit: int = 300):
        return tuple(item for item in self.chats if item.workspace_id == workspace_id)[:limit]

    def create_chat(self, *, workspace_id: str, title: str, active_environment_id: str | None = None) -> ChatRecord:
        now = datetime.now(UTC)
        item = ChatRecord(
            chat_id=new_uuid7(),
            workspace_id=workspace_id,
            title=title,
            status=ChatStatus.ACTIVE,
            active_environment_id=active_environment_id,
            rolling_summary=None,
            created_at=now,
            updated_at=now,
        )
        self.chats.insert(0, item)
        return item


def test_tone_moves_base_toward_white_and_black() -> None:
    base = "#8b5cf6"
    assert tone(base, 0.8) != base
    assert tone(base, -0.8) != base
    assert tone(base, 1.0) == "#ffffff"
    assert tone(base, -1.0) == "#000000"


def test_theme_supports_up_to_four_colors_and_split_mix_modes() -> None:
    split = ThemeConfig(colors=("#22d3ee", "#8b5cf6", "#f59e0b", "#ec4899"), mode="split")
    split_css = theme_css(split)
    assert "#22d3ee" in split_css
    assert "#8b5cf6" in split_css
    assert "#f59e0b" in split_css
    assert "#ec4899" in split_css
    assert ".side" in split_css and ".composer" in split_css

    mixed = theme_css(ThemeConfig(colors=split.colors, mode="mix"))
    assert "body::before" in mixed
    assert "radial-gradient" in mixed


def test_theme_augmentation_is_global_and_defaults_to_purple(monkeypatch) -> None:
    monkeypatch.delenv("AMODEX_THEME_COLORS", raising=False)
    monkeypatch.delenv("AMODEX_THEME_MODE", raising=False)
    page = augment_theme_html('<html lang="pt-BR"><head></head><body></body></html>')
    assert 'id="amodex-global-theme"' in page
    assert "#8b5cf6" in page
    assert 'data-amodex-theme-mode="single"' in page


def test_workspace_v2_has_compact_composer_project_hub_and_tool_drawer() -> None:
    page = augment_workspace_v2_html(WORKSPACE_HTML)
    assert "openProjectsHub()" in page
    assert "globalChatList" in page
    assert "📂" in page
    assert "🌎" in page
    assert "🧠" in page
    assert "🍳" in page
    assert "🎤" in page
    assert "⏫" in page
    assert "JÁ DECIDIDO — NÃO REABRIR" in page
    assert "Gravando… toque 🎤 para finalizar" in page


def test_global_chat_router_uses_one_hidden_container() -> None:
    repository = _FakeWorkspaceRepository()
    app = FastAPI()
    app.include_router(build_global_chat_router(repository))  # type: ignore[arg-type]
    client = TestClient(app)

    first = client.get("/v1/global-workspace")
    assert first.status_code == 200
    assert first.json()["description"] == GLOBAL_WORKSPACE_SENTINEL

    created = client.post("/v1/global-chats", json={"title": "Chat normal"})
    assert created.status_code == 201
    listed = client.get("/v1/global-chats")
    assert listed.status_code == 200
    assert [item["title"] for item in listed.json()] == ["Chat normal"]

    second = client.get("/v1/global-workspace")
    assert second.json()["workspace_id"] == first.json()["workspace_id"]
    assert len([item for item in repository.workspaces if item.description == GLOBAL_WORKSPACE_SENTINEL]) == 1
