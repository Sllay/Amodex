# P0_PERFORMANCE_STRUCTURAL

Status: **VALIDATED**

## Scope

This checkpoint validates P0 Step 3 structural performance work only:

1. Bound runtime PostgreSQL connections to a small per-process pool.
2. Remove whole-upload buffering from the normal attachment path.
3. Preserve the previously validated frontend authority and browser behavior.

This checkpoint intentionally does **not** add caching, does **not** start Step 4, and does **not** deploy to production.

## Validated revision

- Branch: `p0/performance-structural`
- Validated code SHA: `60a8c6f0274134352767221ba9e7100a15c20f17`
- GitHub Actions run: `41400561054`
- Chromium job: `125131263300` — SUCCESS
- WebKit job: `125131262865` — SUCCESS

The checkpoint documentation commit is expected to be newer than the validated code SHA. The SHA above is the exact code revision exercised by the final CI matrix.

## Runtime PostgreSQL pool

`src/amodex/storage/runtime_pool.py` installs a lazy per-process compatibility pool before application repositories perform runtime I/O.

Validated properties:

- `AMODEX_DB_POOL_SIZE` accepts only `1` or `2`; default is `2`.
- At most one or two physical PostgreSQL connections are owned by each application process.
- Existing repository code can continue using `with psycopg.connect(...) as conn:` without opening a new physical connection per method call.
- Normal context exit commits; exceptional exit rolls back.
- Closed/broken connections are discarded instead of being returned to the pool.
- Row factories are reset between checkouts.
- Pool checkout is bounded rather than opening an unbounded number of connections.
- Pool cleanup is registered once with `atexit`, because the pool is a process resource and should not depend on a specific FastAPI lifecycle API version.
- Production migrations remain direct one-shot connections because they execute in their own process before the web runtime.
- Advanced/unknown psycopg connection signatures intentionally bypass the compatibility pool rather than having their semantics changed silently.

The first CI attempt exposed an incompatible `FastAPI.add_event_handler(...)` cleanup call. That coupling was removed; process-level cleanup now belongs to the pool module itself. The final validated run proves application startup and real PostgreSQL traffic after that correction.

## Bounded-memory upload path

The old upload endpoint accumulated the complete request in a Python `bytearray` and then converted it to `bytes` before storage. The Step 3 path replaces that boundary.

Normal upload flow is now:

`request.stream()` → private temporary file → content hash in chunks → media backend from file → attachment record

Validated properties:

- Incoming request chunks are written incrementally to a temporary file.
- Upload size limits are enforced while streaming.
- Empty uploads remain rejected.
- Temporary request files are removed in a `finally` block.
- Local file storage copies in bounded 1 MiB chunks and retains content-addressed SHA-256 references.
- Authenticated Cloudinary upload accepts an open file handle for multipart upload instead of rebuilding the whole object as a Python `bytes` value.
- The previous byte-buffering POST route is removed explicitly during app composition.
- Startup fails closed if exactly one legacy upload route cannot be replaced, preventing two competing upload authorities.
- The existing authenticated Cloudinary `/raw/authenticated` delivery behavior remains preserved.

## Validation evidence

The final CI matrix ran on an isolated PostgreSQL service and HTTPS FastAPI server.

Both Chromium and WebKit passed:

- compilation of the structural performance layer;
- frontend authority regression gate from Step 2;
- pool cap/reuse/transaction tests;
- bounded local file-ingestion tests that forbid `Path.read_bytes()` on the normal path;
- authenticated Cloudinary file-stream tests that forbid `Path.read_bytes()` on the normal path;
- existing Cloudinary media-store tests;
- production migrations on isolated PostgreSQL;
- application startup with the real runtime pool installed;
- complete P0 browser baseline;
- a dedicated browser upload test using a binary attachment larger than 2 MiB through the real `#mmFileInput`, with the returned attachment confirmed in the UI tray.

This means the upload replacement is validated through the browser/API/storage path rather than only by source inspection.

## Explicit limits / non-goals

- The pool is **per process**. Multiple web workers would each own their own one-or-two-connection pool.
- Advanced psycopg connection calls intentionally bypass pooling.
- The rare Cloudinary HTTP 409 existing-object verification path still downloads the existing object to verify the digest; the normal upload path is bounded-memory.
- The local streaming helper currently reuses `FileMediaStore._path_for_ref`, so there is a deliberate internal coupling to that store implementation.
- No cache layer was added in this step.
- The known Step 2 baseline gap remains: a full page reload does not automatically restore which chat was selected, although persisted history remains intact.
- No Render production deployment was performed.
- No production Neon project, old Neon project, or production data was modified.

## Result

`P0_PERFORMANCE_STRUCTURAL = VALIDATED`
