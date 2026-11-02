# P0_FRONTEND_AUTHORITY

Status: **VALIDATED**

## Scope

This checkpoint closes P0 Step 2: replace competing browser-side function wrappers with one frontend runtime authority while preserving the user-visible workspace behavior protected by the P0 browser baseline.

Branch: `p0/web-e2e-baseline`

Validated code SHA: `169ba3261567316ec30f1753724f6ec069d7e909`

Validation workflow: `p0-web-e2e-baseline`

Workflow run: `41392333510`

- Chromium job `125106234529`: **success**
- WebKit job `125106234342`: **success**

The checkpoint document itself is committed after the validated code SHA above and is not part of that tested SHA.

## What changed

### Single frontend authority

`src/amodex/api/ui_stability_web.py` now installs the final browser runtime as `window.AmodexApp`.

It owns the public shared actions and exposes:

- `AmodexApp.state` as the `AppState` facade over the existing durable workspace state;
- `AmodexApp.actions.sendMessage`;
- `AmodexApp.actions.toggleMic`;
- shared API, error and notice helpers;
- canonical message rendering and chat selection bridges;
- a feature hook registry.

Legacy inline handlers remain compatible, but their shared global bridges are bound only by the final runtime instead of being independently wrapped by each feature layer.

### Feature hook contract

The runtime supports explicit hooks:

- `beforeSend`
- `afterOwnerSaved`
- `afterSend`
- `afterChatSelected`
- `afterRenderMessages`
- `toggleMic`

Feature layers contribute behavior through this contract rather than replacing shared actions.

### Multimodal integration

`src/amodex/api/multimodal_web.py` no longer wraps or reassigns `sendMessage`, `selectChat` or `renderMessages`.

Its responsibilities are registered through hooks:

- pending attachments modify the send context in `beforeSend`;
- attachments are linked to the persisted owner message in `afterOwnerSaved`;
- chat selection refreshes multimodal metadata in `afterChatSelected`;
- media decoration occurs in `afterRenderMessages`;
- microphone/STT behavior is provided through the runtime `toggleMic` hook.

### Model Intelligence integration

`src/amodex/api/model_intelligence_web.py` no longer wraps or reassigns `sendMessage`.

Automatic model response is registered as an `afterSend` hook. If no measured free route is eligible, the owner message remains saved and the UI reports the benchmark gate without falling back to a paid route.

### V2 microphone cleanup

`src/amodex/api/workspace_v2.py` no longer installs a `load` handler that reassigns `mmToggleMic`.

The microphone button is owned by the final `AmodexApp` runtime, removing the last known post-load global microphone override from the V2 shell.

## Send boundary and duplicate protection

The owner-message write boundary now has one runtime owner.

The runtime acquires `runtimeState.sending` before feature hooks execute, disables the send control while the write is active, and contains one owner-message `POST /v1/chats/{chat_id}/messages` boundary.

This preserves the P0 double-click guarantee: one user send action cannot create duplicate owner messages through stacked wrappers.

## Architecture regression gate

`tests/unit/test_workspace_frontend_authority.py` is executed before Playwright in the browser workflow.

It proves structural invariants including:

- multimodal and model feature scripts do not contain the old shared-action wrapper patterns;
- `window.AmodexApp` is present as the runtime authority;
- feature scripts register hooks instead of rebinding shared actions;
- the send runtime has a lock and one owner-message write boundary;
- V2 does not rebind `mmToggleMic` after page load.

The workflow fails before browser setup if these invariants regress.

## Browser evidence

The validated SHA was exercised against a real FastAPI application, isolated PostgreSQL, production migrations and HTTPS in CI.

Both Chromium and WebKit passed the existing P0 E2E baseline after the architecture change. The preserved browser flows include:

- passcode login;
- Home with no selected chat;
- mobile navigation drawer;
- project creation;
- project chat creation;
- global chat creation;
- one owner message per send/double-click path;
- persisted history reopened after reload;
- tool drawer;
- disabled STT feedback;
- Connections and Models HTML surfaces.

## Known gap intentionally retained

The P0 baseline still records the selected-chat reload behavior as an expected gap: after a full page reload the application does not automatically restore which chat was selected.

The history itself persists and can be reopened. Automatic selection restoration is not claimed by this checkpoint and remains future local-state/persistence work.

## What this checkpoint does NOT prove

This validation does not constitute live proof of:

- Groq or another external model route producing a real inference response;
- Cloudinary live upload/download behavior;
- GitHub OAuth or Roblox OAuth flows;
- production Render deployment behavior;
- production Neon behavior;
- offline/IndexedDB cache behavior;
- real microphone permission/media capture on physical iOS hardware;
- benchmark quality or availability of an external free provider.

Those require their own explicit evidence and must not be inferred from this checkpoint.

## Production safety

No Render production configuration was changed for this step.

No production Neon database was used by the E2E jobs; CI used its isolated PostgreSQL service.

The old Neon project was not touched.

No paid service or paid fallback was introduced.

## Result

`P0_FRONTEND_AUTHORITY` is **VALIDATED** at code SHA `169ba3261567316ec30f1753724f6ec069d7e909` with Chromium and WebKit both green in run `41392333510`.
