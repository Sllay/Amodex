from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page, expect


E2E_BASE_URL = os.environ.get("AMODEX_E2E_BASE_URL", "https://127.0.0.1:8765").rstrip("/")
E2E_PASSCODE = os.environ.get("AMODEX_E2E_PASSCODE", "246810")


@pytest.fixture(scope="session")
def browser_context_args() -> dict[str, object]:
    """Use an iPhone-XR-like viewport and trust the CI-only self-signed cert."""
    return {
        "ignore_https_errors": True,
        "viewport": {"width": 414, "height": 896},
        "locale": "pt-BR",
    }


@pytest.fixture
def logged_in_page(page: Page) -> Page:
    response = page.goto(E2E_BASE_URL + "/", wait_until="domcontentloaded")
    assert response is not None
    assert response.status == 401
    expect(page.locator("#passcode")).to_be_visible()
    page.locator("#passcode").fill(E2E_PASSCODE)
    page.get_by_role("button", name="Entrar").click()
    page.wait_for_url(E2E_BASE_URL + "/")
    expect(page.locator(".home-stage")).to_be_visible()
    return page


def create_global_chat(page: Page, title: str) -> None:
    page.locator(".global-chat-header .icon-button").click()
    expect(page.locator("#chatModal")).to_have_class(lambda value: value is not None and "open" in value)
    page.locator("#newChatTitle").fill(title)
    page.locator("#chatModal button.primary").click()
    expect(page.locator("#chatTitle")).to_have_text(title)
    expect(page.locator("#composer")).to_be_enabled()
