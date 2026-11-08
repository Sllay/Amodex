# AMODEX supervised V1 — closeout in progress

**V1_BLOCKED.** This is an executable checkpoint A and an explicit external block, not a completed V1 or a release candidate ready to use from an iPhone. The executor now works; the previous capability-query startup error was not reproduced. The current blocker is free-model runtime configuration, distinct from executor availability.

Branch: `release/amodex-v1-supervised`. Starting checkout confirmed at **`af79465c4920d269d5823e854cda295c8b7abe93`**, clean. Checkpoint A code/test SHA: **`eeb2603`** (full SHA in [commands.json](evidence/v1-supervised/commands.json)). The delivery message supplies the final report commit SHA; `git rev-parse HEAD` on the delivered release branch identifies that documentation/evidence commit. No merge or deployment occurred.

## Checkpoints

| Checkpoint | Evidence/status |
| --- | --- |
| A — consolidation | 65 distinct targeted tests pass; migrations upgrade/reentry/transactional rollback proven locally; two test defects corrected |
| B — Web → real free Hermes model → Luau | BLOCKED before inference: no approved free-provider credential/billing configuration in this executor; product handoff remains to be implemented |
| C — mobile flow | Not delivered/validated in this checkpoint; existing Web UI preserved |
| D — real demonstration | Not performed; no simulated result is counted as a real model demonstration |
| E — launch preparation | Local schema/rollback evidence and existing deployment contracts retained; production/staging readiness unverified; no release approved |

The invoked [release gate](V1_SUPERVISED_RELEASE_GATE.md) requires an already configured, verified free provider and says: **“Stop at the first genuine external blocker instead of attempting undocumented workarounds or repeating external effects.”** The work stops at that boundary after preserving the safe consolidation checkpoint. It does not select a paid fallback, set an unknown account to `free`, borrow production credentials, or fabricate inference evidence.

## Functionality actually available

The branch already contains R1 machine authentication/ownership, R2A Run lease/fencing, R3 idempotent runner intake/dispatch, R4 process/heartbeat supervision and R2B transactional terminal receipts. Existing Web/workspace/chat/draft components and scoped Hermes read/search/patch/validation tools are present. The Roblox tool and historic pure-fixture cloud evidence remain intact; production modules for these components were not rewritten.

Those are component capabilities. The existing draft API still rejects marking a draft EXECUTED without a real Run handoff (`DRAFT_EXECUTION_REQUIRES_RUN_HANDOFF`). **This checkpoint does not implement that missing Web-to-Hermes coding handoff or a new user-facing execution endpoint.** A terminal Runner Job is not a validated Run, and neither previous deterministic agent tests nor earlier Roblox fixture execution prove the requested final product flow.

Implemented here: a runnable, offline `scripts/v1_supervised_preflight.py`, migration release tests and corrected regression fixtures. The preflight reuses the existing provider credential-pool configuration, outputs metadata only, and never certifies quota, inference or release acceptance. Even a synthetically configured free key in its unit tests produces only CONFIGURED_REQUIRES_LIVE_VALIDATION, never PASS.

## Real-model evidence and exact external dependency

The preflight was run against the **actual current Codex process**, without modifying environment variables. It exited **2**, `BLOCKED_NO_APPROVED_FREE_PROVIDER`:

| Supported pool | Credential bound | Billing class |
| --- | --- | --- |
| gemini-free-primary | false | unknown |
| groq-free-primary | false | unknown |
| openrouter-free-primary | false | unknown |
| nvidia-free-primary | false | unknown |

No quota was verified and no inference was attempted. The attached Codex configuration reports the Roblox HTTP secret ready, but that is not a model credential and does not establish a Render/runner grant. These observations apply to this executor; they do not assert that the owner's accounts or deployed service lack credentials elsewhere. No authorized staging URL/context supplying a free model was provided during this checkpoint.

Smallest next action: supply **one** already approved free provider on the authorized demonstration host, via host secret settings and an explicitly verified free billing/quota grant, with that provider's outbound destination permitted. The existing registry uses `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY` or `NVIDIA_API_KEY` and the corresponding `AMODEX_<PROVIDER>_BILLING_CLASS`. Do not post values in chat or commit them. If the grant exists only on staging, identify the authorized staging context rather than copying production secrets into this chat.

Then resume B: connect durable owner approval/Run/task scope to the existing Hermes worker, bound real inference and tool execution, persist an inspectable code artifact and result, and build/validate its mobile projection. **Supplying a credential alone does not complete this missing implementation or prove the V1.** Staging mobile acceptance still follows afterward.

## Consolidation and tests

Actual PostgreSQL **17.11**, Python **3.13.5**, pytest **9.0.2**. Only disposable loopback databases named `amodex_qa_*` were used, with explicit local guards. No Neon connection or production write occurred.

- Applied the complete 15-migration manifest to a fresh local database.
- Tested upgrade from the 13-file pre-R3/R2B schema to the current 15-file schema, retaining legacy terminal data without fabricating receipts or dispatch authorization.
- Proved reentrancy and fail-closed schema readiness before/after the two missing migrations.
- Rehearsed PostgreSQL transactional DDL rollback of 0008/0009 before commit; prior columns/data/manifest remain intact.
- Rechecked critical R1 authentication/receipt access, R2A stale authority, R3 concurrent intake/acquisition, R2B rollback/concurrent reports/read-only reconciliation, R4 descendant termination/cancellation/control timeout, and the existing deterministic Hermes-tool edit path.

Eight Model Intelligence failures were reproduced, not hidden: the test `FakeRegistry.client_for` lacked the production `operation` keyword. Updating the fixture to accept and validate `chat`/`benchmark` restored the original test assertions. **No production routing/model fix or real inference is claimed.** One R4 process test was timing-dependent: it injected loss at the second heartbeat before children necessarily announced readiness. It now injects loss after the process readiness handshake, without increasing sleeps or weakening cleanup assertions.

Final result: **65 distinct tests passed, zero final failures/errors/skips**, using the latest result per case. Initial failures and their corrections are recorded in the evidence. No xfail, ignored failure or fake release PASS was introduced. [JUnit](evidence/v1-supervised/checkpoint-a.xml), [checkpoint summary](evidence/v1-supervised/checkpoint-a.json), [exact commands and provenance](evidence/v1-supervised/commands.json), [actual provider preflight](evidence/v1-supervised/provider-preflight.json).

## Mobile state

The existing UI and prior interface fixes are unchanged. No iPhone/Safari or deployed mobile end-to-end acceptance was performed here. The full request → approval → coding → local validation → durable result flow is not exposed by this checkpoint; the owner cannot be instructed to use it as a delivered feature. No desktop Studio dependency was added, and no Roblox experience was accessed.

## Migrations, deployment and rollback

No application migration, runtime authority change, dependency or deployment configuration was added. Migration ordering remains the production manifest, including 0008 and 0009. The deployment contract remains [Render/Neon/Cloudinary V1](../DEPLOY_RENDER_NEON_CLOUDINARY_V1.md): guarded matching pooled/direct Neon endpoints, distinct liveness `/healthz` and readiness `/readyz`, and published SHA verification through the running service.

The rollback rehearsal proves **uncommitted local DDL rollback**, not permission to drop committed production receipts or histories. For this test/preflight-only checkpoint, application rollback is checkout/redeployment of the starting SHA while preserving the additive schema and data. No production SQL rollback, Render cutover or Neon reset was proposed or performed. Actual deployed SHA, staging readiness, free allowance and mobile operation remain unverified; no production access is needed to preserve the current work.

## Remaining limits and outcome

Critical blockers: missing approved free-model configuration here; missing product handoff; no real-model artifact/result demonstration; no final mobile or staging validation. Therefore **V1_RC_ONLY would be inaccurate**: the missing item is not merely deployment validation. The result is **V1_BLOCKED**, with a verified partial checkpoint and the dependency above.

R5–R10, cross-host autonomous recovery, arbitrary Studio editing and publishing remain excluded. No new Core, Director, Worker, scheduler or agent framework was created. No paid inference, GitHub Actions dispatch, Roblox Open Cloud request, publication, production migration, merge or deploy was executed. No UNKNOWN external operation was repeated; none was initiated in this attempt.

The final report/README commit only records this status and the evidence. Preserve checkpoint A and resume B after the approved runtime context is available, rather than repeating the already proven migrations or prior Roblox fixture tests.
