from __future__ import annotations

import re
import uuid

import pytest
from playwright.sync_api import Page, expect

from .conftest import E2E_BASE_URL, create_global_chat, open_projects


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def test_private_gate_home_and_mobile_drawer(logged_in_page: Page) -> None:
    page = logged_in_page
    expect(page.locator(".home-stage h1")).to_have_text("Amodex")
    expect(page.locator(".composer-wrap")).to_have_class(re.compile(r"\bui-v2-composer-hidden\b"))

    page.locator(".mobile-menu").click()
    expect(page.locator("#side")).to_have_class(re.compile(r"\bopen\b"))
    expect(page.get_by_role("button", name="Fechar menu")).to_be_visible()
    page.get_by_role("button", name="Fechar menu").click()
    expect(page.locator("#side")).not_to_have_class(re.compile(r"\bopen\b"))


def test_project_chat_send_once_and_reload_history(logged_in_page: Page) -> None:
    page = logged_in_page
    project = unique("E2E-Projeto")
    chat = unique("E2E-Chat")
    message = unique("mensagem-unica")

    open_projects(page)
    page.locator(".project-hub button.primary").click()
    page.locator("#newWorkspaceName").fill(project)
    page.locator("#newWorkspaceDescription").fill("Baseline Playwright")
    page.locator("#workspaceModal button.primary").click()

    expect(page.locator(".project-detail h1")).to_have_text(project)
    page.locator(".project-detail-head button.primary").click()
    page.locator("#newChatTitle").fill(chat)
    page.locator("#chatModal button.primary").click()

    expect(page.locator("#chatTitle")).to_have_text(chat)
    page.locator("#composer").fill(message)
    page.locator("#uiSend").click()
    owner_message = page.locator(".message.owner .bubble", has_text=message)
    expect(owner_message).to_have_count(1)

    page.reload(wait_until="domcontentloaded")
    expect(page.locator(".home-stage")).to_be_visible()
    open_projects(page)
    page.locator(".project-card", has_text=project).click()
    expect(page.locator(".project-detail h1")).to_have_text(project)
    page.locator(".project-chat-row", has_text=chat).click()
    expect(page.locator("#chatTitle")).to_have_text(chat)
    expect(page.locator(".message.owner .bubble", has_text=message)).to_have_count(1)


def test_double_click_send_writes_one_owner_message(logged_in_page: Page) -> None:
    page = logged_in_page
    chat = unique("E2E-Global")
    message = unique("duplo-clique")
    create_global_chat(page, chat)

    page.locator("#composer").fill(message)
    page.locator("#uiSend").dblclick(delay=35)
    expect(page.locator(".message.owner .bubble", has_text=message)).to_have_count(1)


def test_tool_drawer_and_disabled_stt_feedback(logged_in_page: Page) -> None:
    page = logged_in_page
    create_global_chat(page, unique("E2E-STT"))

    page.locator("#toolToggle").click()
    drawer = page.locator("#toolDrawer")
    expect(drawer).to_have_class(re.compile(r"\bopen\b"))
    expect(drawer.get_by_role("button", name="Arquivos", exact=True)).to_be_visible()
    expect(drawer.get_by_role("button", name="Pesquisar na web", exact=True)).to_be_visible()
    expect(drawer.get_by_role("button", name="Memória", exact=True)).to_be_visible()
    expect(drawer.get_by_role("button", name="Cozinhar", exact=True)).to_be_visible()

    page.locator("#mmMic").click()
    expect(page.locator("#composerMeta")).to_contain_text("Transcrição indisponível")


def test_connections_and_models_surfaces_return_html(logged_in_page: Page) -> None:
    page = logged_in_page
    response = page.goto(E2E_BASE_URL + "/connections", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    expect(page.locator("body")).to_contain_text("Connections")

    response = page.goto(E2E_BASE_URL + "/models", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    expect(page.locator("body")).to_contain_text("Model")


@pytest.mark.xfail(
    reason="P0 baseline gap: selected chat is not restored after a full page reload yet",
    strict=False,
)
def test_reload_restores_selected_chat_baseline_gap(logged_in_page: Page) -> None:
    page = logged_in_page
    chat = unique("E2E-Restore")
    create_global_chat(page, chat)
    expect(page.locator("#chatTitle")).to_have_text(chat)

    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#chatTitle")).to_have_text(chat)
