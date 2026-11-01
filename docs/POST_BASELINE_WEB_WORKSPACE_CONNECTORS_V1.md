# Web Workspace + Connectors V1

This post-baseline program changes the primary Amodex user experience from CLI-first to web-first without moving authority out of the Control Plane.

## Scope discipline

The program is deliberately split into checkpoints:

1. **Web Workspace** — Project workspaces, chats, project memory, execution drafts and a mobile-first browser shell.
2. **Connectors UX** — GitHub App and Roblox Open Cloud connector contracts/resources and visual connection surfaces.
3. Multimodal Chat — explicitly out of scope for checkpoints 1–2.
4. Model Intelligence — explicitly out of scope for checkpoints 1–2.

Termux/CLI remains an administrative and recovery client. The web UI and Termux call the same Control Plane APIs.

## Checkpoint 1 — Web Workspace

### Product hierarchy

```text
Workspace Project
├── Project Memory
├── Chats
│   ├── OWNER messages
│   ├── attachment metadata
│   └── Execution Drafts
└── bound Environments
    └── existing Environment / Session / Run authority
```

A Workspace is an organizational/conversational namespace. It is **not** an execution authority.

An Environment remains the isolation boundary for execution. A Workspace may bind multiple Environments, but one Environment may not belong to multiple Workspaces.

### Chat authority

The public chat API only accepts `OWNER` messages. Browser clients cannot create `AMODEX` messages and therefore cannot impersonate a future model broker.

AI inference is intentionally reported as `GATED_BENCHMARK_PENDING`. Checkpoint 1 does not promote Stage 5 model candidates or bypass provider lifecycle policy.

### Project memory

New memory enters as `CANDIDATE`. It becomes canonical only after an explicit `APPROVED` decision. Rejected memory is retained for audit/history but omitted from normal memory projection.

This prevents one casual sentence or weak research result from silently contaminating project memory.

### Execution Drafts

A Draft contains:

- title;
- objective;
- instructions;
- validation plan;
- optional bound Environment.

Draft state may move among `DRAFT`, `READY` and `CANCELLED` through the workspace surface.

The workspace API refuses to mark a Draft `EXECUTED`. That state requires a future real Run handoff so the conversational layer cannot claim that work happened when no Environment/Run executed it.

### Attachments

Checkpoint 1 stores **metadata only** for an already-authorized storage reference. It does not upload bytes, fetch arbitrary URLs, invoke vision models or perform STT. Those capabilities belong to Step 3 and are intentionally not implemented here.

### Web composition

Preferred web entrypoint:

```bash
uvicorn amodex.api.workspace_app:app --host 0.0.0.0 --port 8000
```

The web-first app reuses the exact existing Control Plane FastAPI object and preserves every existing `/v1/...` endpoint. It only replaces the root projection:

- `/` → mobile-first Workspace/Chat UI;
- `/legacy` → previous technical dashboard;
- `/docs` → existing FastAPI API documentation.

### Required migration

After Stage 6 and post-baseline migrations `0001` and `0002`, apply:

```text
migrations/postbaseline/0003_web_workspace.sql
```

Workspace endpoints fail explicitly with `WORKSPACE_SCHEMA_NOT_READY` when the migration is absent.

### Checkpoint 1 validation contract

Checkpoint 1 is not valid unless CI proves:

- workspace schema on PostgreSQL;
- existing Control Plane routes still work;
- mobile-first root surface renders;
- one Environment cannot bind to two Workspaces;
- a Chat/Draft cannot target an Environment outside its Workspace;
- public clients cannot write `AMODEX` messages;
- memory defaults to `CANDIDATE` and explicit approval works;
- Draft cannot self-report `EXECUTED`;
- Stage 6 and Environment Product regressions remain green;
- workspace modules do not import the Hermes worker implementation;
- repository hygiene remains clean.

## Checkpoint 2 — Connectors UX

Checkpoint 2 is implemented only after Checkpoint 1 has an exact validated SHA. Its detailed operational contract is appended when implemented.
