# Hermes Agent Baseline for Amodex

Status: **Research baseline — Part 1/3**

Date checked: **2026-10-31**

This document records what Hermes Agent currently provides, why it is the leading runtime substrate for Amodex, which parts Amodex should reuse, and which responsibilities must remain outside Hermes.

This is not a commitment to fork Hermes. The preferred architecture is composition/wrapping first, fork only if a required extension cannot be implemented cleanly through the supported plugin/runtime boundaries.

## 1. Project identity

The relevant upstream project is **Hermes Agent by Nous Research**, not merely the Hermes family of language models.

Primary sources:

- Official docs: https://hermes-agent.nousresearch.com/docs
- Architecture: https://hermes-agent.nousresearch.com/docs/developer-guide/architecture
- Agent loop: https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop
- Security: https://hermes-agent.nousresearch.com/docs/user-guide/security
- MCP: https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp
- GitHub: https://github.com/NousResearch/hermes-agent

The upstream repository is MIT-licensed at the time of this baseline. That permits Amodex to depend on, wrap, modify, or fork Hermes code subject to the MIT notice requirements for upstream material. Amodex's own repository remains governed by its proprietary license.

## 2. Current Hermes architecture that matters to Amodex

The current Hermes documentation describes a modular codebase with these important pieces:

- `run_agent.py` as the `AIAgent` facade;
- agent loop logic under `agent/conversation_loop.py` and turn-phase modules;
- `agent/prompt_builder.py` for prompt assembly;
- pluggable `agent/context_engine.py`;
- default context compression/summarization;
- `model_tools.py` and a central tool registry/dispatch system;
- provider runtime resolution shared by CLI/gateway/cron/ACP surfaces;
- SQLite-based persistent session state with FTS5 and lineage;
- MCP client integration;
- plugin discovery from user/project/pip sources;
- terminal environment backends;
- gateway/session routing;
- cron/scheduled agent tasks;
- ACP integration for editor-native use.

The architecture already separates a platform-agnostic agent core from multiple user surfaces. That is valuable because Amodex also needs one engineering core reachable from Termux first and other surfaces later.

## 3. Agent loop behavior

Hermes' documented loop already handles important generic agent-runtime concerns:

```text
build prompt
   |
   v
resolve/call provider
   |
   v
tool calls?
  | yes
  v
execute registered tools
  |
  +----> append results -> model again

text/final result
  |
  v
persist session
```

It also documents retry/error phases, context compression, callbacks, and persistence.

This is exactly the category of code Amodex should avoid rewriting from scratch without a concrete reason.

## 4. Session persistence: useful but not sufficient

Hermes persists conversations/sessions and supports resumption. That solves a different problem from Amodex's durable engineering state.

Hermes session state can preserve:

- conversation messages;
- session lineage;
- memory/context-related state;
- resumable chat history.

Amodex still needs an external durable control-plane model for:

- task DAG state;
- worker leases;
- file ownership;
- fencing tokens;
- provider quota/cost state;
- checkpoint classes;
- validation evidence;
- promotion state;
- idempotency records;
- approvals;
- run recovery after side effects.

Therefore:

> Hermes session persistence is a worker-runtime feature, not the Amodex source of truth for project execution.

The initial Amodex control-plane database candidate remains PostgreSQL.

## 5. Provider layer

Hermes currently documents shared provider runtime resolution across its surfaces and support for many provider/API modes.

This is highly reusable for:

- provider authentication resolution;
- model aliases;
- OpenAI-compatible endpoints;
- common provider request plumbing;
- credential-pool behavior where appropriate;
- model switching inside a worker runtime.

Amodex should **not** immediately add LiteLLM on top of Hermes merely because LiteLLM is popular. That risks creating two provider-routing layers with overlapping responsibilities.

The correct decision sequence is:

1. enumerate Amodex provider requirements;
2. test Hermes native provider/runtime behavior against them;
3. identify concrete gaps;
4. add an adapter/gateway only for gaps that cannot be cleanly implemented through Hermes or Amodex's outer Provider Governor.

## 6. Amodex Provider Governor remains external authority

Even if Hermes performs provider calls, Amodex requires a deterministic outer governor.

The Governor owns policy such as:

- `zero_cost` eligibility;
- per-provider/model concurrency;
- known RPM/TPM/RPD windows;
- task priority/fairness;
- queueing;
- `Retry-After` handling;
- exponential backoff + jitter;
- circuit breaker state;
- cooldown after repeated provider failure;
- capability equivalence for fallback;
- owner-approved paid exceptions;
- aggregate usage telemetry.

Hermes can be the request execution runtime while Amodex decides whether a request is allowed to be attempted at all.

## 7. Tools and toolsets

Hermes' central registry/toolset model is a strong fit.

Amodex should prefer to expose tools through bounded capability sets rather than giving every worker every registered tool.

Example:

```text
research worker
  -> web/search/read-only repository tools

coding worker
  -> scoped file patch + tests + limited shell

Godot worker
  -> scoped repository tools + Serena + selected Godot MCP capabilities

review worker
  -> read-only diff/evidence/diagnostic tools
```

Tool availability must be derived from the Task Package and policy layer, not from the worker asking to enable more tools on its own.

## 8. MCP integration

Hermes currently supports local stdio and remote HTTP MCP servers, automatic discovery/registration, and per-server filtering.

This is a major reason to use Hermes as the worker substrate.

For Amodex, MCP policy is stricter:

- project-local MCP definitions are discovered from project configuration;
- servers start within the Runner/trust boundary where practical;
- environment variables are filtered;
- only task-required tools are exposed;
- dangerous or broad MCP capabilities can require policy approval;
- MCP output is treated as tool output, not higher-priority instruction text.

For Black-Islands, the intended pattern is:

```text
Amodex worker
   |
   +--> Serena MCP / semantic layer
   |
   +--> Godot MCP
          |
          +--> Godot editor bridge/runtime
```

## 9. Terminal backends and Runner strategy

Hermes currently documents multiple terminal/environment backends, including local/container/remote or serverless styles depending on installed capabilities/plugins.

This is valuable, but Amodex's `RunnerBackend` should remain its own control-plane abstraction.

Why:

- Northflank job lifecycle is an Amodex hosting concern;
- a runner owns Git branch/worktree and task lease semantics;
- runner creation is governed by zero-cost capacity;
- recovery needs to correlate Runner identity with task/checkpoint state;
- a future backend may use GitHub Actions or owner-provided infrastructure.

Hermes may operate **inside** a Runner and may itself use a terminal backend inside that Runner. Amodex must not confuse Hermes terminal-environment selection with durable job scheduling authority.

## 10. Context engine

Hermes provides a pluggable context engine and documented compression behavior.

Amodex should reuse that extensibility but inject an Amodex-produced `ContextCapsule`.

The distinction:

```text
Amodex Context Engine
  -> decides which project facts/authority/symbols belong in the task

Hermes context engine
  -> manages conversation/context-window mechanics during worker execution
```

Hermes compression is useful after context is already correct. It is not a substitute for repository selective disclosure.

## 11. Memory and skills

Hermes has persistent memory and a self-improving skills system.

These features are potentially useful but require governance before enabling them broadly in Amodex.

### Useful directions

- reusable safe procedures for tool/bootstrap workflows;
- provider-specific recovery procedures;
- language/framework debugging procedures;
- project-local operational skills;
- lessons from validated failures.

### Risks

- a self-written skill could silently broaden behavior;
- stale skills could conflict with project ADRs;
- learned procedures could carry assumptions from one project to another;
- memory could capture secrets or untrusted instructions;
- skill evolution could make runs less reproducible.

Initial Amodex policy should therefore treat worker-created skills as **proposals**, not automatically trusted executable policy.

A promoted skill should have provenance, scope, version, validation, and owner/project classification.

## 12. Delegation and Bot Mode

Hermes supports delegation/subagents and specialist bots. Amodex must not adopt the social/delegation topology as its primary governance mechanism.

Default Amodex behavior:

- workers cannot spawn arbitrary persistent teams;
- worker-to-worker free chat is disabled/not used;
- delegation depth is one unless explicitly authorized;
- the Amodex Orchestrator owns worker creation;
- task DAG and path ownership determine concurrency;
- reports return to the Director/Orchestrator.

Hermes delegate functionality may be used internally only when it can be wrapped by these constraints or disabled for workers that do not need it.

## 13. Security baseline we should preserve

Hermes currently documents defense-in-depth controls covering:

- user authorization;
- approval for dangerous commands;
- file-write safety;
- container isolation;
- MCP credential/environment filtering;
- context-file prompt-injection scanning;
- cross-session isolation;
- working-directory/input sanitization.

Amodex should preserve and test these protections rather than bypass them for convenience.

Amodex additionally needs project-level controls not covered by generic agent security:

- task path leases;
- protected Git branches;
- zero-cost policy;
- GitHub permission minimization;
- promotion authority;
- runner fencing;
- secret scoping by task;
- durable side-effect idempotency;
- project decision/stability authority.

## 14. Blank-slate principle

Hermes currently documents a “Blank Slate” setup mode where most optional systems are disabled until explicitly enabled.

That philosophy matches Amodex well.

Amodex workers should start from the minimum tool/capability set needed for their task instead of loading every Hermes feature by default.

This also reduces:

- attack surface;
- context/tool-schema overhead;
- accidental side effects;
- provider token cost;
- unexpected self-improvement/delegation behavior.

## 15. Fork vs wrapper decision

Current recommendation: **wrapper/composition first**.

Prefer:

```text
Amodex Control Plane
      |
      v
Amodex Worker Adapter
      |
      v
Hermes runtime APIs/extensions
```

Fork Hermes only when one of these becomes true:

- required safety/authority behavior cannot be enforced externally or through plugins/hooks;
- required lifecycle APIs are not exposed and upstream will not accept a suitable change;
- upstream behavior creates unacceptable coupling or nondeterminism;
- a critical patch must be maintained independently for a bounded period.

If a fork becomes necessary, keep the divergence minimal and track upstream commits/releases explicitly.

## 16. What Amodex should not copy from Hermes blindly

Do not automatically inherit:

- unrestricted delegation;
- social multi-bot collaboration as the project execution model;
- every enabled toolset;
- worker-owned cron/scheduling authority;
- SQLite session DB as the entire platform state store;
- provider routing that ignores Amodex zero-cost/quota policy;
- self-evolving skills as immediately trusted policy;
- project-wide memory without project/secret boundaries.

## 17. Initial integration seam

The future worker adapter should conceptually expose:

```text
start_worker(
  task_package,
  context_capsule,
  tool_capabilities,
  provider_lease,
  runner_workspace,
  event_callback,
  cancellation_token
)
```

The adapter translates Amodex authority into Hermes runtime configuration and translates Hermes tool/results/events back into Amodex structured run events.

The exact API is not frozen until Part 2 inspects the current upstream callable interfaces/code more deeply.

## 18. Required Hermes PoCs before Stage 1/2 freeze

### HERMES-P01 — Embedded worker lifecycle

Prove Amodex can start one Hermes worker programmatically, supply scoped context/tools, receive tool events, cancel it, and capture final structured outcome without depending on interactive CLI state.

### HERMES-P02 — Provider interception/governor

Prove the Amodex Provider Governor can authorize/deny/queue provider usage before Hermes spends quota, including a simulated rate-limit/circuit-breaker path.

### HERMES-P03 — Scoped MCP

Start one MCP server from a Runner, expose only selected capabilities, verify environment filtering, invoke a harmless tool, then terminate cleanly.

### HERMES-P04 — Recovery boundary

Kill a worker process after a durable Git checkpoint/event, recreate it, and resume the Amodex task from external durable state without assuming the original Hermes process still exists.

### HERMES-P05 — Disable uncontrolled delegation

Prove a worker lacking delegation capability cannot create subagents merely through prompt intent.

## 19. Decision summary

### Adopt/reuse if PoCs confirm

- agent loop;
- provider plumbing;
- tool registry/runtime;
- MCP client integration;
- plugin extension mechanisms;
- conversation/context-window handling;
- interrupt/callback patterns;
- selected security controls;
- optional memory/skills after governance.

### Amodex-owned

- Technical Director authority;
- task DAG;
- worker scheduler;
- path ownership;
- durable task/run state;
- zero-cost enforcement;
- provider capacity governor;
- repository Context Engine;
- component stability;
- decision ledger;
- Git checkpoints/candidates/promotion;
- validation evidence authority;
- RunnerBackend lifecycle;
- cross-worker integration policy.

### Not yet proven

- best programmatic embedding interface for the exact Hermes release used by implementation;
- whether native Hermes provider plumbing covers every targeted free provider cleanly;
- whether hooks/plugins are sufficient to avoid a fork for all enforcement points;
- resource cost of Hermes + project toolchain inside Northflank free runners;
- best persistence split between Hermes local session state and Amodex task records.

Those are PoC items, not excuses to create placeholder integrations.
