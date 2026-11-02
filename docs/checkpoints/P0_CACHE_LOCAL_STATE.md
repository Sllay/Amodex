# P0_CACHE_LOCAL_STATE

Status: **VALIDATED**

## Scope

This checkpoint validates P0 Step 4 local-first cache/state behavior only:

1. Private ETag/304 revalidation for UI snapshots.
2. Browser memory + IndexedDB as a fast local projection.
3. Immediate restoration of the last project/chat and recent history.
4. PostgreSQL/Neon remains the durable authority and is revalidated in the background.
5. Service Worker caches only the private application shell/static assets for offline navigation.
6. Private API/auth payloads are never placed in Service Worker CacheStorage.
7. The frontend authority and Step 3 performance boundaries remain preserved.

No production deployment is part of this checkpoint.

## Validated revision

- Branch: `p0/cache-local-state`
- Validated code SHA: `4cf96b035d38cd1d13a35135d19108edc8f27f64`
- GitHub Actions run: `41408833324`
- Chromium job: `125156944646` — SUCCESS
- WebKit job: `125156945145` — SUCCESS

The checkpoint documentation commit is expected to be newer than the validated code SHA. The SHA above is the exact code revision exercised by the final CI matrix.

## Private ETag / 304 revalidation

UI snapshot GETs now emit deterministic content-derived ETags and explicit private cache policy:

- `Cache-Control: private, no-cache, max-age=0, must-revalidate`
- `Vary: Cookie`
- `X-Amodex-Cache: private-revalidate`
- deterministic SHA-256 `ETag`

A matching `If-None-Match` returns `304` with no JSON body.

This is used on the batched UI projections for bootstrap, project, chat, and connection snapshots. The browser explicitly owns the cached body in IndexedDB; shared/public HTTP caches are not used as the private data store.

## Browser local-first hierarchy

The browser layer uses the following order:

`JS memory → IndexedDB → background HTTP ETag revalidation → PostgreSQL/Neon authority`

Validated behavior:

- recent bootstrap/project/chat snapshots are kept in memory for the current page;
- durable browser-local snapshots are stored in IndexedDB;
- the last UI target is stored locally (`projects`, project folder, or chat + scope/workspace);
- boot hydrates from local state before waiting for the database;
- project/chat selection uses a cached snapshot immediately when available;
- background revalidation sends the saved ETag;
- `304` keeps the current local snapshot without retransmitting the full JSON body;
- `200` replaces the local projection with the authoritative server snapshot;
- normal writes continue through existing server APIs, then refresh the local projection;
- PostgreSQL/Neon remains the durable source of truth.

## Full reload restoration

The old P0 baseline gap where a full page reload returned to Home has been closed.

Validated in both Chromium and WebKit:

- create/open a chat;
- send and persist a message;
- cache the chat snapshot locally;
- full page reload;
- the same selected chat is restored automatically;
- recent message history is restored;
- composer returns enabled for the restored chat;
- background server synchronization still occurs afterward.

## Service Worker / offline shell

The Service Worker is intentionally narrow:

- caches the authenticated application shell `/`;
- may cache same-origin static file types;
- never intercepts/caches `/v1/*`;
- never intercepts/caches `/auth/*`;
- never caches `/sw.js` itself;
- navigation is network-first with cached shell fallback;
- private chat/project payloads remain in IndexedDB rather than CacheStorage;
- old Amodex shell-cache versions are removed on activation.

The final browser acceptance test performs a real offline navigation:

1. open a chat and persist a message;
2. confirm the chat snapshot exists in IndexedDB;
3. confirm the shell exists in CacheStorage and an active Service Worker exists;
4. reload once online;
5. disable browser network access;
6. perform a real page reload;
7. require the application shell to load;
8. require the same chat title and message history to be restored locally;
9. require the UI to report cached/offline state.

Both Chromium and WebKit passed this behavior.

## Cache/privacy boundaries

The root application shell is marked private/no-cache and varies by Cookie.

Logout responses send:

- `Cache-Control: no-store`
- `Clear-Site-Data: "cache", "storage"`

This intentionally removes the offline/private local state when the user logs out.

A `401` during background revalidation also clears Amodex local private data and redirects to the passcode surface.

## Frontend authority preservation

Step 4 does not introduce a second public application state authority.

Composition order remains intentional:

1. Workspace V2 base behavior
2. local-first cache augmentation
3. Step 2 `ui_stability` / `AmodexApp` final public frontend authority

The cache layer projects into the existing Workspace V2 state and participates through existing feature hooks. It does not add another public `AppState`, another send authority, or another independent UI runtime.

The Step 2 frontend-authority regression suite passes in the final Step 4 matrix.

## Step 3 regression preservation

The Step 4 matrix also reruns the structural performance gates from Step 3:

- bounded PostgreSQL runtime pool;
- streaming attachment pipeline;
- authenticated Cloudinary file-stream behavior;
- >2 MiB browser upload path.

Those regressions remained green.

## Validation evidence

The final CI matrix validates:

- Python compilation;
- frontend authority regression;
- Step 3 structural performance regression;
- local-cache/ETag/Service-Worker unit boundaries;
- Cloudinary media regression;
- production migrations on isolated PostgreSQL;
- batched UI snapshot integration tests;
- application startup;
- full browser baseline;
- ETag → 304 through the real browser/server path;
- last-chat restoration after full reload;
- real offline reload with shell from Service Worker and chat/history from IndexedDB.

Final result:

- Chromium — SUCCESS
- WebKit — SUCCESS

## CI Service Worker origin note

The cache-specific Service Worker/offline acceptance matrix uses `http://localhost` intentionally.

Browsers treat `localhost` as a trustworthy development origin for Service Workers. Earlier attempts used a CI-only self-signed HTTPS certificate on `127.0.0.1`; WebKit completed the real offline test, while Chromium exposed unstable Service Worker page-control behavior under that synthetic certificate and could hang during offline navigation despite the worker and shell cache existing.

The final harness removes that certificate-specific variable while preserving the actual product requirement: the network is disabled and the browser must perform a real offline reload successfully.

The previous P0 browser baseline continues to validate HTTPS/access-gate behavior separately. Production remains expected to run behind valid HTTPS.

## Explicit limits / non-goals

- Browser cache state is local to the browser profile/device; it is not cross-device cache synchronization.
- Background synchronization happens during boot/navigation/revalidation; this step does not add push, realtime subscriptions, or cross-tab synchronization.
- A stale local snapshot may appear briefly before background revalidation replaces it. That is deliberate local-first behavior.
- Service Worker CacheStorage contains shell/static assets only; private API payloads belong in IndexedDB.
- Logout intentionally removes cached private/offline state.
- IndexedDB is a performance/offline projection, not a durable server authority.
- No production deployment was performed.
- No production Neon database was modified by this validation.
- The user's old Neon project was not touched.

## Result

`P0_CACHE_LOCAL_STATE = VALIDATED`
