# Stage 0 — Final Result

Status: **COMPLETE**

Date: 2026-10-31

Stage 0 exits under the normative gates in `docs/IMPLEMENTATION_PROGRAM.md` and `docs/DEFINITION_OF_DONE.md`.

The purpose of Stage 0 was to retire blocking feasibility risk before production runtime construction. It did not attempt to build the full Amodex product.

## Final closeout evidence

Successful closeout workflow:

```text
workflow: stage0-closeout
run:      41199986168
job:      124533716919
source:   d85343d1ea955f9ab46eccb66b8595fa2fd1fd33
```

All closeout steps passed:

```text
checkout exact Stage 0 revision                         PASS
install pinned contract toolchain                     PASS
re-run executable contract baseline                   PASS
validate all blocking Stage 0 decisions               PASS
compile retained Stage 0 Python research              PASS
reject tracked configured secret values               PASS
repository hygiene gate                               PASS
assert temporary Stage 0 Git refs are zero            PASS
re-audit P0-06B Cloudflare/Neon fixture namespace     PASS
Stage 0 closeout verdict                              PASS
```

The canonical contract suite also re-ran at the closeout SHA:

```text
Python:       3.13.5
uv:           0.10.0
pytest:       14 passed
ToolCall JSON schema generation: PASS
uv.lock sha256: b0c4eb6f74a569e98957e27916e206f01d69f173f26b92db5184c58d2b91470c
```

## Final PoC decisions

```text
P0-01  ADOPT — executable contracts / Python baseline
P0-02  ADAPT — Hermes bounded worker runtime
P0-03  ADOPT — real zero-cost Gemini provider path
P0-04  ADAPT — PostgreSQL claim / lease / fencing
P0-05  ADAPT — Git/GitHub expected-SHA + reconciliation
P0-06A REJECT_AS_PRIMARY_FOR_CURRENT_ACCOUNT — Northflank account capacity
P0-06B ADAPT — Cloudflare Workers + Neon event-driven Control Plane
P0-07  ADAPT — disposable General Runner lifecycle + external cancellation
P0-08  ADAPT — Godot 4.7.2 headless Runner capability
P0-09  ADAPT — Godot windowed/Xvfb/input/screenshot Runner capability
P0-10  DEFERRED — required before claiming verified multi-backend Runner portability
```

No blocking PoC remains in `RETEST`.

## Architecture changes frozen by evidence

### Control Plane hosting

Accepted in:

`docs/decisions/ADR-0001-event-driven-control-plane-hosting.md`

Frozen baseline:

```text
Cloudflare Worker
  = thin stateless/event-driven Control Plane surface

Neon PostgreSQL
  = durable authority/state

PostgreSQL lease/fencing
  = initial coordination substrate

Cloudflare Cron
  = periodic reconciliation/maintenance wakeup only

RunnerBackend
  = long-running execution
```

Cloudflare Queues were not proven in Stage 0 and are not implicitly adopted.

### Runner strategy

Accepted in:

`docs/decisions/ADR-0002-initial-runner-backend-strategy.md`

Frozen baseline:

```text
RunnerBackend remains backend-neutral.

GitHub Actions is the first concrete disposable backend only when
repository/account policy classifies the execution as zero-cost.

Private-repository minutes are not treated as unlimited free compute.
No paid fallback may occur silently.
```

P0-10 remains intentionally open. Amodex does **not** yet claim verified portability across two concrete Runner backends.

## Major feasibility conclusions

### Hermes

Hermes can be embedded as a bounded worker substrate without making Hermes session state, provider choice, delegation, or tool discovery authoritative over Amodex.

### Providers

At least one real free external model path is proven with structured output and tool/function behavior while paid fallback remains disabled.

### Persistence / recovery

PostgreSQL is sufficient for initial job claiming, heartbeats, lease expiry/reclaim, fencing, stale-worker rejection, and atomic state/event behavior without introducing Redis.

### Git / GitHub

Expected-SHA mutation, stale-base protection, ambiguous acknowledgement reconciliation, candidate/promotion authority separation, and remote recovery were exercised with real GitHub state.

### General Runner

A disposable runner can checkpoint remotely, terminate, reconstruct on another machine, and be cancelled by an independent controller without losing the SAFE checkpoint.

### Godot headless

The Black-Islands-like pinned toolchain is technically viable on a disposable Runner:

```text
Godot       4.7.2
Node.js     22.23.4
Godot MCP   0.1.2
Serena      2.1.0
```

Real LSP, parser/import, runtime boot, focused test, MCP protocol, addon and headless scene backend were proven.

### Godot visual

Real windowed Godot under Xvfb/Mesa llvmpipe was proven with:

- live MCP runtime bridge;
- game-tree inspection;
- native input;
- before/after screenshots;
- stable screenshot hashes across repeated runs;
- performance/runtime observations;
- CI artifact evidence;
- no orphaned Godot/display processes.

## Security / hygiene closeout

The final closeout proves at the Stage 0 repository level:

- configured provider/hosting secret values are not tracked;
- no `.env`, private-key-style files, CI PNG/ZIP/log evidence or `.godot` runtime tree is committed in the research roots;
- no temporary `p0-05-*`, `p0-07-*` or equivalent Stage 0 Git refs remain;
- P0-06B namespaced Cloudflare/Neon fixture resources are absent after audit;
- non-namespaced owner resources are not touched by the cleanup probe.

## Known limitations carried forward

These are explicit limitations, not hidden Stage 0 blockers:

1. P0-10 has not proven the same RunnerSpec on two concrete backends.
2. GitHub Actions is not considered unlimited zero-cost compute for private repositories such as Black-Islands.
3. Black-Islands was read only during Stage 0; no write benchmark was authorized or performed.
4. Cloudflare Cron activation latency makes it suitable for maintenance/reconciliation, not immediate dispatch.
5. Cloudflare Queues are not adopted by Stage 0 evidence.
6. Godot performance observations are hosted Linux/software-render proxy evidence, not physical mobile-device validation.
7. Full production scheduler, Provider Governor, Hermes adapter, API, persistence repositories, Termux UI and coding worker do not yet exist; those belong to later stages.

## Exit decision

**Stage 0: COMPLETE.**

The next normative stage is:

```text
Stage 1 — Durable remote runtime and provider layer
```

Stage 1 must build the first production vertical slice:

```text
client
 -> remote API
 -> durable Run/Task state
 -> PostgreSQL scheduler/lease
 -> zero-cost Provider Governor
 -> real free provider request
 -> durable result/events
 -> client reconnects and reads completion
```

It must also prove crash/restart recovery and fail-closed cost behavior before exiting.
