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

Validated exact checkpoint SHA:

```text
12a70cadfc7c3ac373187ff73b8aa743755a5cd5
```

The `postbaseline-web-workspace-connectors` push workflow passed PostgreSQL integration, the Step 1 authority/isolation tests, Environment Product + Stage 6 compatibility regressions, Hermes implementation isolation, the Step 3/4 scope guard and repository hygiene on that exact SHA.

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

The web-first app reuses the exact existing Control Plane FastAPI object and preserves every existing `/v1/...` endpoint. It only changes product projections:

- `/` → mobile-first Workspace/Chat UI;
- `/connections` → connector/resource management after Checkpoint 2;
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

Checkpoint 2 is layered on top of the already validated Checkpoint 1. It does not change the Environment execution authority or provider/model lifecycle.

### Connector hierarchy

```text
Workspace Project
└── Connector Account
    ├── GitHub account / GitHub App installation access
    │   └── GITHUB_REPOSITORY resources
    └── Roblox Open Cloud account
        ├── ROBLOX_UNIVERSE resources
        └── ROBLOX_PLACE resources
             └── optional Environment binding
```

A connector Resource can only bind to an Environment already bound to the same Workspace. Cross-Workspace resource binding fails closed.

### Credential boundary

PostgreSQL never stores raw OAuth access tokens, refresh tokens, PKCE verifiers or raw OAuth state values.

The database stores only:

- opaque `credential_ref` values;
- SHA-256 OAuth `state_hash` values;
- opaque PKCE `verifier_ref` values;
- account/resource metadata required for product projection.

OAuth state is single-use, provider-bound and expires. Reuse, provider mismatch and expiry fail closed.

Connector credentials live behind `CredentialVault`:

- `MemoryCredentialVault` exists only for deterministic tests/local-process experiments;
- `FileCredentialVault` is the zero-cost durable implementation used when `AMODEX_CONNECTOR_VAULT_DIR` is configured;
- the vault directory is forced to mode `0700` and credential records to `0600`;
- vault paths are opaque and guarded against traversal;
- deployment must place the vault outside the web root and on private/encrypted host storage where available;
- missing vault configuration makes connectors report `NOT_CONFIGURED` instead of silently persisting secrets somewhere else.

Provider client secrets, when required, are referenced as `env://UPPER_CASE_NAME`; the reference may be durable, the secret value may not.

### GitHub App connector

The GitHub path uses OAuth + PKCE and the user-facing GitHub App installation flow.

Required deployment configuration:

```text
AMODEX_GITHUB_APP_SLUG
AMODEX_GITHUB_APP_CLIENT_ID
AMODEX_GITHUB_APP_REDIRECT_URI
```

Optional confidential-client secret reference:

```text
AMODEX_GITHUB_APP_CLIENT_SECRET_REF=env://AMODEX_GITHUB_APP_CLIENT_SECRET
```

The OAuth callback does **not** trust a query-string `installation_id` as proof of repository authority. After authenticated OAuth completion, Amodex discovers installations through GitHub's authenticated API and enumerates repositories actually visible through each installation. Those repositories become `GITHUB_REPOSITORY` Resources.

Repository access selection therefore remains controlled by the GitHub App installation rather than an arbitrary repository name typed into the browser.

### Roblox Open Cloud connector

Required deployment configuration:

```text
AMODEX_ROBLOX_OAUTH_CLIENT_ID
AMODEX_ROBLOX_OAUTH_REDIRECT_URI
AMODEX_ROBLOX_OAUTH_SCOPES
```

Optional confidential-client secret reference:

```text
AMODEX_ROBLOX_OAUTH_CLIENT_SECRET_REF=env://AMODEX_ROBLOX_OAUTH_CLIENT_SECRET
```

The connector uses OAuth + PKCE, resolves the authenticated Roblox identity through `userinfo`, and can project authorized Universe resources from the Open Cloud token resource response when the configured client permits that operation.

Known Universe/Place IDs can also be validated through stable Open Cloud Universe/Place endpoints before becoming `ROBLOX_PLACE` Resources. A typed ID is therefore not accepted as a durable resource until Roblox confirms it through the authenticated API.

### Visual UX

`/connections` is the web-first connector surface. It supports:

- selecting the current Workspace;
- seeing configuration/readiness for GitHub and Roblox;
- starting the provider authorization flow;
- viewing connected accounts;
- refreshing authorized Resources;
- adding a validated Roblox Place by Universe ID + Place ID;
- choosing an Environment and binding a Resource to it;
- disconnecting an account and deleting its vault credential;
- returning to the main Workspace without using Termux commands.

The main Workspace exposes a visual `Connections` action pointing to `/connections`.

### Required migration

After `0003_web_workspace.sql`, apply:

```text
migrations/postbaseline/0004_connectors.sql
```

Connector endpoints fail explicitly with `CONNECTOR_SCHEMA_NOT_READY` when this migration is absent.

### External setup boundary

The repository now contains the connector implementation and deterministic provider-contract tests, but a real production account connection still requires external registration/configuration of:

- a GitHub App with its callback URL and desired repository permissions;
- a Roblox Open Cloud OAuth application with its callback URL and scopes;
- `AMODEX_CONNECTOR_VAULT_DIR` on the deployed Control Plane host;
- the corresponding environment configuration/secret references.

CI uses injected HTTP transports and a memory vault to prove OAuth/PKCE/resource behavior without placing real user/provider credentials in GitHub Actions. Passing CI must **not** be described as a live production GitHub or Roblox account connection.

### Checkpoint 2 validation contract

Checkpoint 2 is not valid unless the exact final SHA proves all of the following:

- the entire exact Checkpoint 1 regression still passes;
- connector schema works on PostgreSQL;
- OAuth authorization uses PKCE;
- raw OAuth state is not stored in PostgreSQL;
- OAuth state is single-use;
- raw provider access tokens do not appear in connector database rows;
- GitHub repository discovery comes from authenticated installation enumeration rather than an untrusted callback `installation_id`;
- Roblox Universe and Place resources are projected only after authenticated provider responses;
- a connector Resource cannot bind to an Environment outside its Workspace;
- disconnect revokes the connector product state and deletes the vault credential;
- GitHub/Roblox configuration state is visible through the Web/API;
- Environment Product and Stage 6 regressions remain green;
- web/connector modules remain independent from Hermes implementation;
- Step 3/4 capability leakage guards remain green;
- repository hygiene remains clean.

## Explicit stop after Checkpoint 2

This branch intentionally does **not** implement:

- binary upload or image attachment bytes;
- vision routing;
- STT/TTS;
- web-research execution from chat;
- model quality benchmarks/leaderboards;
- model promotion/demotion/routing changes;
- autonomous chat inference.

Those belong to Steps 3 and 4 and require a separate user-directed continuation after this checkpoint.
