"""Local-only browser audit. Known defects are strict XFAIL, never approvals.

Reuse the workspace fixtures; no provider calls, external auth or production writes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from .conftest import E2E_BASE_URL, E2E_PASSCODE

VIEWPORTS = [(1440, 900), (414, 896), (390, 844), (768, 1024)]


def evidence(page: Page, name: str) -> None:
    directory = Path(os.environ.get("AMODEX_QA_ARTIFACT_DIR", "artifacts/qa/browser-audit"))
    directory.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(directory / f"{name}.png"))
    (directory / f"{name}.json").write_text(
        json.dumps({"viewport": page.viewport_size, "path": page.url.split("?")[0]}),
        encoding="utf-8",
    )


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_workspace_navigation_at_required_viewports(logged_in_page: Page, width: int, height: int) -> None:
    page = logged_in_page
    page.set_viewport_size({"width": width, "height": height})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    if width <= 700:
        page.locator(".mobile-menu").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#side")).to_have_class("side open")
    page.locator(".project-launch").click()
    expect(page.locator(".project-hub h1")).to_have_text("Projetos")
    page.locator(".project-hub button.primary").click()
    expect(page.locator("#newWorkspaceName")).to_be_visible()
    evidence(page, f"project-modal-{width}x{height}")
    page.locator("#workspaceModal").get_by_role("button", name="Cancelar", exact=True).click()
    expect(page.locator("#newWorkspaceName")).not_to_be_visible()
    assert errors == []


def test_wrong_local_passcode_feedback_then_keyboard_login(page: Page) -> None:
    response = page.goto(E2E_BASE_URL + "/")
    assert response is not None and response.status == 401
    wrong = "000000" if E2E_PASSCODE != "000000" else "111111"
    page.locator("#passcode").fill(wrong)
    page.locator("#passcode").press("Enter")
    expect(page.locator("body")).to_contain_text("Código inválido")
    # Never capture the entered passcode, even in this synthetic local fixture.
    page.locator("#passcode").clear()
    evidence(page, "invalid-local-login")
    page.locator("#passcode").fill(E2E_PASSCODE)
    page.locator("#passcode").press("Enter")
    expect(page.locator(".home-stage")).to_be_visible()


def test_missing_session_requires_login_again(logged_in_page: Page) -> None:
    page = logged_in_page
    page.context.clear_cookies()
    response = page.reload()
    assert response is not None and response.status == 401
    expect(page.locator("#passcode")).to_be_visible()
    expect(page.locator(".home-stage")).to_have_count(0)


@pytest.mark.parametrize("width,height", [(414, 896), (390, 844)])
@pytest.mark.xfail(strict=True, reason="QA-01: Contexto control is clipped outside the mobile viewport")
def test_context_control_fits_mobile_viewport(logged_in_page: Page, width: int, height: int) -> None:
    page = logged_in_page
    page.set_viewport_size({"width": width, "height": height})
    button = page.locator(".topbar .mobile-context")
    expect(button).to_be_visible()
    box = button.bounding_box()
    assert box is not None
    evidence(page, f"context-clipping-{width}x{height}")
    assert box["x"] >= 0 and box["x"] + box["width"] <= width


@pytest.mark.xfail(strict=True, reason="QA-02: project modal does not close with Escape")
def test_project_modal_closes_with_escape(logged_in_page: Page) -> None:
    page = logged_in_page
    page.set_viewport_size({"width": 1440, "height": 900})
    page.locator(".project-launch").click()
    page.locator(".project-hub button.primary").click()
    expect(page.locator("#newWorkspaceName")).to_be_visible()
    page.keyboard.press("Escape")
    evidence(page, "modal-after-escape")
    expect(page.locator("#newWorkspaceName")).not_to_be_visible(timeout=1000)


@pytest.mark.xfail(strict=True, reason="QA-02: project modal lacks dialog semantics and initial focus")
def test_project_modal_has_dialog_semantics_and_focus(logged_in_page: Page) -> None:
    page = logged_in_page
    page.set_viewport_size({"width": 1440, "height": 900})
    page.locator(".project-launch").click()
    page.locator(".project-hub button.primary").click()
    expect(page.get_by_role("dialog", name="Novo projeto")).to_be_visible(timeout=1000)
    expect(page.locator("#newWorkspaceName")).to_be_focused()


@pytest.mark.xfail(strict=True, reason="QA-03: Model Intelligence header overflows at 390 pixels")
def test_model_header_fits_small_mobile(logged_in_page: Page) -> None:
    page = logged_in_page
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(E2E_BASE_URL + "/models", wait_until="networkidle")
    evidence(page, "models-small-mobile")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
