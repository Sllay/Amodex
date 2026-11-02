# P0 Web E2E Baseline — VALIDATED

Status: **VALIDATED**

## Scope

This checkpoint establishes the first real browser baseline for the Amodex web workspace. It intentionally does **not** refactor the frontend architecture, add caching, change production, or promote anything to `main`.

The goal is to prove the current product shell in a real browser against the real FastAPI application and a real PostgreSQL schema before starting the P0 UI-authority refactor.

## Branch and evidence

- Branch: `p0/web-e2e-baseline`
- Base deployment commit: `772ea7cc61bacbefc70ff0284a779048603ea984`
- Validated test payload commit: `b5ae2c0a0290f61161f92853f4bbd26e0f5f7e97`
- GitHub Actions workflow: `p0-web-e2e-baseline`
- Validated run: `41390843550`
- Chromium job: `125101663595` — success
- WebKit job: `125101663965` — success

## Test environment

The E2E suite runs in CI with:

- Python 3.13.5.
- Pinned `playwright==1.64.1` and `pytest-playwright==0.10.0` installed only in the E2E workflow.
- Browser matrix: Chromium and WebKit.
- Mobile viewport: 414 × 896, `pt-BR` locale.
- The real FastAPI/Uvicorn application running over CI-only HTTPS so the production-style `Secure` + `__Host-` session cookie can be exercised by a real browser.
- An isolated PostgreSQL 16 service populated through the production migration runner.
- Local file media backend for the browser baseline.
- STT explicitly disabled.
- Test-only passcode/session values.

This checkpoint does **not** use the production Neon project, Render production service, Cloudinary production account, Groq production key, or any old Neon project.

## Proven browser flows

The browser baseline proves, in both Chromium and WebKit:

1. Access Gate behavior from an unauthenticated page load through passcode login.
2. Home state with no selected chat and the composer hidden.
3. Mobile sidebar opens and closes through visible user controls.
4. Projects view opens from the mobile sidebar.
5. Creating a project returns the user directly into that project's view.
6. Creating a project chat returns the user directly into the chat.
7. Sending an owner message persists exactly one visible owner message.
8. Reloading the application, manually reopening the project and chat, and loading the persisted history works.
9. Double-clicking the send button does not produce duplicate owner writes; the browser flow results in one message.
10. Global chat creation works through the mobile UI.
11. The tool drawer exposes Arquivos, Pesquisa, Memória and Cozinhar controls.
12. With STT disabled, the microphone reports the unavailable transcription state instead of pretending to work.
13. `/connections` renders as a browser page under the authenticated session.
14. `/models` renders as a browser page under the authenticated session.

## Known product gap captured by the baseline

The current application does **not** automatically restore the selected chat after a full browser reload.

That behavior is represented as an explicit non-strict `xfail` in the E2E suite. The existing server-side history is durable, but the browser has no local navigation/session-state persistence yet. This is a real product gap and should be addressed later by the planned client state/cache work rather than hidden by the baseline.

## Explicitly not proven here

This checkpoint does **not** claim that the following are functionally validated in production:

- live Groq STT;
- live Cloudinary upload/download/delete;
- real model inference or successful model benchmark execution;
- live GitHub App installation/OAuth resource flow;
- live Roblox OAuth/resource flow;
- offline cache, IndexedDB, Service Worker or PWA behavior;
- production Render cold-start behavior;
- production Neon latency;
- production secrets/quotas;
- real mobile Safari hardware behavior beyond Playwright WebKit.

Those need separate integration/smoke evidence.

## Baseline files

- `tests/e2e/conftest.py`
- `tests/e2e/test_workspace_baseline.py`
- `.github/workflows/p0-web-e2e-baseline.yml`

## Checkpoint decision

**P0-WEB-BASELINE = VALIDATED**

The next work may proceed to the frontend consolidation step: replace successive global JS wrappers with a single explicit UI authority/state architecture while preserving the validated browser behavior above.

No merge, deployment or promotion is authorized by this checkpoint alone.
