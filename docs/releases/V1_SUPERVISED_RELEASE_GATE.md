# AMODEX V1 — supervised mobile-first release gate

**Status:** WORK IN PROGRESS — not released, not deployed, and not equivalent to the normative **V1 Safe** gate in `docs/DEFINITION_OF_DONE.md`.

**Base:** `fix/runner-terminal-receipts-r2b` at `20ee7c45c3c9df576a68f0f2bdada2358c3c42d3`. This ancestry already includes the UI/timeout improvements, R1, R2A, Roblox first slice, task 08, R3, R4 and R2B. Do not cherry-pick or reimplement these without a demonstrated gap.

## Scope: what a person must be able to do from an iPhone

An authorized owner must be able to open the existing AMODEX Web interface, select an authorized Environment/project, request a **small Luau coding change**, and observe a **real, bounded, free-provider model invocation**, authorized file edit, local Luau validation, optional single supervised Roblox Open Cloud execution, and a trustworthy result/evidence/status in the Web UI. No desktop Studio dependency is permitted for this MVP path. The code result must be recoverable and inspectable in a Git branch/checkpoint or equivalent durable authorized artifact; production game publication is not in scope.

A scripted deterministic test proves plumbing, **not** real model execution. A Luau Open Cloud fixture test proves logic execution, **not** Roblox gameplay/Studio edits. Do not call either one an end-to-end autonomous agent test.

## Release-critical gates

1. **Consolidation and compatibility:** branch ancestry and schema checked; apply migrations in correct order on an isolated disposable PostgreSQL database, especially `0008_runner_dispatch_idempotency.sql` and `0009_runner_terminal_receipts.sql`. Verify upgrade from prior schema, reentrancy, read compatibility, and fail-closed readiness. Production Neon `Amodex` database is never reset and its endpoint guard must be respected. Never access an older Neon project. Existing failing Model Intelligence tests must be triaged and fixed if relevant or listed as explicit release blockers; do not hide with xfail/skip.
2. **Single bounded vertical slice:** connect existing Web/owner authority -> durable Run/Task/Job -> authorized scoped Hermes tooling -> approved local Luau validation -> structured evidence -> durable result/projection. Reuse Director/Worker/runtime/Runner and R1–R4, R2A/R2B; no parallel worker framework or scheduler. Persist required identity/scope/attempt state *before* external effects. If a restart cannot safely resume, show BLOCKED/UNKNOWN and require human reconciliation, never automatic replay.
3. **Real inference and zero-cost:** one limited real inference through an already configured approved **free** provider and Hermes, only when credentials and free allowance are verified; explicit timeout/token/tool-step bounds; paid fallback disabled, credit exhaustion blocks. Do not disclose provider secrets in logs, UI, diffs or artifacts. If no approved free provider works, status BLOCKED, not PASS.
4. **Roblox target authority:** supervised integration may use only Universe `10769712453` and Place `118200154785968`, with explicit resource/Environment/task/source-hash approval; never touch an experience named `Teste - Não entre`. Max one POST for an expressly authorized pure Luau test; no retry for ambiguous effect, no publication, SavePlaceAsync, DataStore writes, economy operations or broad cloud permissions. The Codex HTTP secret does **not** imply the Web/Render host has access; use separate host grants.
5. **Web mobile usability:** build on the existing UI; clearly show target/environment, request, approval, run phase, outcome, logs/evidence, cost/free status and UNKNOWN/BLOCKED state; do not imply terminal Runner Job means validated Run. No credentials in the browser. Support iPhone viewport and touch. No desktop-only prerequisite.
6. **Validation, Git, release:** deterministic integration/fault tests for denial paths, idempotent retries, lost ACK, stale authority, wrong environment/resource, unauthorized paths and model/tool failures; one real bounded end-to-end model demonstration through the actual product route. Evidence must name SHA/config/version and distinguish mock/local/cloud/model results. Use SAFE/VALIDATED checkpoint/gates and separate promotion approval; never write directly to main. Test migration/rollback with isolated DB before proposing production migration or deployment.
7. **Operations and honesty:** current production may use Render + guarded Neon + Cloudinary per `docs/DEPLOY_RENDER_NEON_CLOUDINARY_V1.md`; do not equate liveness `/healthz` with readiness `/readyz`. Verify actual deployed SHA and UI from an authorized staging/pilot route. No automatic paid resources, production schema changes, main merge or service cutover without a successful release gate and explicit operator approval.

## Required final outcome labels

- **V1_SUPERVISED_PASS:** all applicable gates genuinely proven on final SHA, including actual mobile Web -> real authorized model -> file/tool -> validation -> durable result, plus deploy readiness verified. This does **not** mean full V1 Safe or automatic long-running Recovery.
- **V1_RC_ONLY:** code/tests pass but staging/deployed end-to-end is still unverified.
- **V1_BLOCKED:** a critical capability is missing, no free-provider access, migrations conflict, real E2E fails or safety gate blocks. State the exact blocker and smallest next action.

## Deliberate exclusions

Full R5–R10 recovery/reconciliation/checkpoint automation beyond the minimum safe persisted path, arbitrary Studio editing, visual gameplay bot, automatic Roblox publication, cross-host recovery of UNKNOWN external effects, unlimited autonomous loops and broad multi-project rollout. Preserve the roadmap; do not mislabel this supervised V1 as the normative `V1 Safe`.

## Implementation workflow

Use short checkpoint commits on this release branch with proofs after each: (A) consolidate/migrations/regressions, (B) authenticated Web->agent integration with one bounded real model, (C) mobile UI/validation/evidence, (D) release readiness/rollback/report. Stop at the first genuine external blocker instead of attempting undocumented workarounds or repeating external effects. Track changes and status in `docs/releases/V1_SUPERVISED_CLOSEOUT.md`. Do not claim an artifact exists if no runnable path reaches it.

User outcome matters more than green test counts: the V1 is not done until the owner can use the actual mobile Web path to request, supervise and inspect one real Luau coding task.
