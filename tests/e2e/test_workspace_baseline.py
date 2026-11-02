from __future__ import annotations

import re
import uuid

from playwright.sync_api import Page, expect

from .conftest import E2E_BASE_URL, create_global_chat, open_projects


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def wait_for_local_cache(page: Page) -> None:
    page.wait_for_function("window.AmodexLocalCache && window.AmodexLocalCache.ready")
    page.evaluate("() => window.AmodexLocalCache.ready()")


def wait_for_shell_cache(page: Page) -> None:
    page.evaluate("() => window.AmodexLocalCache.serviceWorkerReady()")
    page.wait_for_function(
        """async () => {
          if (!('caches' in window)) return false;
          return Boolean(await caches.match('/'));
        }""",
        timeout=15_000,
    )
    active = page.evaluate(
        """async () => {
          if (!('serviceWorker' in navigator)) return false;
          const registration = await navigator.serviceWorker.ready;
          return Boolean(registration.active);
        }"""
    )
    assert active is True


def test_private_gate_home_and_mobile_drawer(logged_in_page: Page) -> None:
    page = logged_in_page
    expect(page.locator(".home-stage h1")).to_have_text("Amodex")
    expect(page.locator(".composer-wrap")).to_have_class(re.compile(r"\bui-v2-composer-hidden\b"))

    page.locator(".mobile-menu").click()
    expect(page.locator("#side")).to_have_class(re.compile(r"\bopen\b"))
    expect(page.get_by_role("button", name="Fechar menu")).to_be_visible()
    page.get_by_role("button", name="Fechar menu").click()
    expect(page.locator("#side")).not_to_have_class(re.compile(r"\bopen\b"))


def test_project_chat_send_once_and_reload_restores_history(logged_in_page: Page) -> None:
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
    wait_for_local_cache(page)

    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#chatTitle")).to_have_text(chat)
    expect(page.locator(".message.owner .bubble", has_text=message)).to_have_count(1)
    expect(page.locator("#composer")).to_be_enabled()


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


def test_large_attachment_upload_reaches_streaming_pipeline(logged_in_page: Page) -> None:
    page = logged_in_page
    create_global_chat(page, unique("E2E-Upload"))
    filename = unique("streaming") + ".bin"
    payload = (b"amodex-stream-boundary-" * 100_000) + b"tail"
    assert len(payload) > 2 * 1024 * 1024

    page.locator("#mmFileInput").set_input_files(
        {
            "name": filename,
            "mimeType": "application/octet-stream",
            "buffer": payload,
        }
    )

    chip = page.locator("#mmTray .mm-chip", has_text=filename)
    expect(chip).to_have_count(1, timeout=15_000)
    expect(chip).to_contain_text(filename)


def test_private_etag_revalidation_returns_304(logged_in_page: Page) -> None:
    page = logged_in_page
    result = page.evaluate(
        """async () => {
          const first = await fetch('/v1/ui/bootstrap', {cache: 'no-store'});
          const etag = first.headers.get('etag');
          const policy = first.headers.get('cache-control') || '';
          const second = await fetch('/v1/ui/bootstrap', {
            cache: 'no-store',
            headers: {'If-None-Match': etag},
          });
          return {firstStatus:first.status, etag, policy, secondStatus:second.status};
        }"""
    )
    assert result["firstStatus"] == 200
    assert result["etag"]
    assert "private" in result["policy"]
    assert "public" not in result["policy"]
    assert result["secondStatus"] == 304


def test_last_global_chat_restores_after_reload(logged_in_page: Page) -> None:
    page = logged_in_page
    chat = unique("E2E-Restore")
    message = unique("restore-message")
    create_global_chat(page, chat)
    page.locator("#composer").fill(message)
    page.locator("#uiSend").click()
    expect(page.locator(".message.owner .bubble", has_text=message)).to_have_count(1)
    page.wait_for_function(
        """async () => {
          if (!window.AmodexLocalCache || !state.chat) return false;
          const entry = await AmodexLocalCache.getSnapshot(`chat:${state.chat.chat_id}`);
          return Boolean(entry?.payload?.messages?.some(m => m.content.includes('restore-message')));
        }""",
        timeout=10_000,
    )

    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#chatTitle")).to_have_text(chat)
    expect(page.locator(".message.owner .bubble", has_text=message)).to_have_count(1)


def test_cached_chat_survives_offline_reload(logged_in_page: Page) -> None:
    page = logged_in_page
    chat = unique("E2E-Offline")
    message = unique("offline-message")
    create_global_chat(page, chat)
    page.locator("#composer").fill(message)
    page.locator("#uiSend").click()
    expect(page.locator(".message.owner .bubble", has_text=message)).to_have_count(1)

    page.wait_for_function(
        """async () => {
          if (!window.AmodexLocalCache || !state.chat) return false;
          const entry = await AmodexLocalCache.getSnapshot(`chat:${state.chat.chat_id}`);
          return Boolean(entry?.payload?.messages?.length);
        }""",
        timeout=10_000,
    )
    wait_for_shell_cache(page)

    # Give the active worker one normal navigation before the real acceptance
    # check. Browser-specific controller timing is not the product contract: the
    # next navigation must actually succeed while the network is disabled.
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#chatTitle")).to_have_text(chat)
    assert page.evaluate(
        """async () => Boolean((await navigator.serviceWorker.ready).active)"""
    ) is True

    page.context.set_offline(True)
    try:
        page.reload(wait_until="domcontentloaded")
        expect(page.locator("#chatTitle")).to_have_text(chat)
        expect(page.locator(".message.owner .bubble", has_text=message)).to_have_count(1)
        expect(page.locator("#statusBadge")).to_contain_text(re.compile("offline|cached"))
    finally:
        page.context.set_offline(False)


def test_connections_and_models_surfaces_return_html(logged_in_page: Page) -> None:
    page = logged_in_page
    response = page.goto(E2E_BASE_URL + "/connections", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    expect(page.locator("body")).to_contain_text("Connections")

    response = page.goto(E2E_BASE_URL + "/models", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    expect(page.locator("body")).to_contain_text("Model")
