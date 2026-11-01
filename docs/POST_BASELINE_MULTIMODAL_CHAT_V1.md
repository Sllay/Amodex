# Post-baseline Multimodal Chat V1

This document defines **Step 3 only** of the web-first Amodex product program.

Step 3 adds explicit multimodal actions to the existing Workspace chat while preserving the existing Control Plane, Environment authority, provider cost policy, Git promotion gates and Hermes isolation.

It does **not** implement Step 4 model intelligence, quality benchmarks, automatic model routing, model promotion/demotion or a model leaderboard.

## Product scope

The Workspace chat gains:

- bounded binary file uploads;
- image previews and explicit image analysis;
- microphone recording in supported browsers;
- explicit speech-to-text for audio attachments;
- text-to-speech through an optional free server provider with browser `speechSynthesis` fallback;
- explicit web research through a configured SearXNG endpoint;
- durable audit records for multimodal operations;
- durable message-to-attachment links.

These are **user-triggered tools**. They do not turn the ordinary chat send action into automatic AI inference.

The normal chat inference boundary remains gated until Step 4.

## Authority model

```text
Workspace Project
└── Chat
    ├── OWNER messages
    ├── Attachment metadata
    │   └── opaque content-addressed media reference
    └── Multimodal operations
        ├── VISION
        ├── STT
        ├── TTS
        └── WEB_RESEARCH

Environment
└── remains the execution authority for project work
```

Multimodal operations may add an internal `AMODEX` message only through trusted server code. The public chat-message endpoint still rejects a browser/client attempting to impersonate `AMODEX`.

A multimodal action does not create a Run, modify a repository or bypass Environment ownership.

## Required migration

After the existing post-baseline migrations, apply:

```text
migrations/postbaseline/0005_multimodal_chat.sql
```

The migration adds:

- `amodex_workspace_message_attachments`;
- `amodex_multimodal_operations`.

PostgreSQL stores metadata, relationships, operation results and provenance. It does **not** store uploaded binary bodies.

## Binary media storage

Set a private media directory on the Control Plane host:

```text
AMODEX_MEDIA_STORE_DIR=/private/path/amodex-media
```

Optional upload limit:

```text
AMODEX_MEDIA_MAX_UPLOAD_BYTES=26214400
```

The default is 25 MiB. The implementation rejects values above 100 MiB.

The zero-cost durable media store is content-addressed:

```text
media://sha256/<digest>
```

Properties:

- directory mode `0700`;
- media object mode `0600`;
- SHA-256 verification on read;
- atomic write/replace;
- path traversal rejection;
- database contains the opaque `media://...` reference rather than raw bytes.

`MemoryMediaStore` exists for deterministic tests and local-process experiments only.

If `AMODEX_MEDIA_STORE_DIR` is absent, binary upload fails closed instead of silently storing files somewhere else.

## Vision

Vision is explicit and optional.

Enable only after choosing a compatible provider/model whose current account policy is deliberately classified as free:

```text
AMODEX_VISION_ENABLED=true
AMODEX_VISION_PROVIDER=openrouter
AMODEX_VISION_MODEL=<vision-capable-model>
AMODEX_VISION_BILLING_CLASS=free
```

Optional overrides:

```text
AMODEX_VISION_BASE_URL=<openai-compatible-base-url>
AMODEX_VISION_API_KEY_REF=env://OPENROUTER_API_KEY
```

For known OpenAI-compatible providers already present in Amodex, the client can reuse their configured API base/key reference defaults.

The provider credential value itself is not stored in PostgreSQL. It is resolved from an `env://UPPER_CASE_VARIABLE` reference at call time.

If the capability is not explicitly classified `free`, the operation is recorded as `BLOCKED` and no provider request is sent.

Step 3 does not decide which vision model is best. That quality/selection problem belongs to Step 4.

## Speech-to-text

The Step 3 server STT adapter uses the Groq OpenAI-compatible transcription surface and defaults to:

```text
whisper-large-v3-turbo
```

Configuration:

```text
AMODEX_STT_ENABLED=true
AMODEX_STT_MODEL=whisper-large-v3-turbo
AMODEX_STT_API_KEY_REF=env://GROQ_API_KEY
AMODEX_STT_BILLING_CLASS=free
```

If `AMODEX_STT_BILLING_CLASS` is omitted, the adapter may reuse the existing `AMODEX_GROQ_BILLING_CLASS` owner policy. Anything other than explicit `free` is blocked.

The browser microphone flow records an audio attachment first. When STT is ready, transcription is then requested explicitly by the product flow. The transcript is placed in the composer for the owner to inspect/edit; the server does not automatically send it as an OWNER message.

## Text-to-speech

The web client always has a best-effort browser TTS path using the browser's `speechSynthesis` API when available.

Server TTS is optional and remains zero-cost gated:

```text
AMODEX_TTS_ENABLED=true
AMODEX_TTS_PROVIDER=<openai-compatible-provider>
AMODEX_TTS_BASE_URL=<provider-base-url>
AMODEX_TTS_MODEL=<tts-model>
AMODEX_TTS_VOICE=<voice>
AMODEX_TTS_API_KEY_REF=env://PROVIDER_KEY
AMODEX_TTS_BILLING_CLASS=free
AMODEX_TTS_RESPONSE_FORMAT=mp3
```

If server TTS is unavailable or blocked, the web UI falls back to browser speech synthesis instead of silently switching to a paid provider.

Generated server audio is stored through the same content-addressed media store and becomes a chat attachment.

## Web research

Step 3 uses a configured SearXNG instance as the zero-cost search abstraction:

```text
AMODEX_RESEARCH_SEARXNG_URL=https://your-searxng.example
```

The current Step 3 contract sends a search request and records returned titles, URLs, snippets and search-engine provenance.

It deliberately **does not fetch arbitrary result pages**. `page_fetch=false` is recorded in provenance. This keeps the first research slice bounded and avoids turning the Control Plane into an unrestricted URL fetcher/SSRF surface.

A later research capability may add controlled page retrieval only with an explicit network/security policy.

## Multimodal API

Key endpoints:

```text
GET  /v1/multimodal/status
POST /v1/chats/{chat_id}/uploads
GET  /v1/attachments/{attachment_id}/content
POST /v1/messages/{message_id}/attachments
GET  /v1/chats/{chat_id}/attachment-links
GET  /v1/chats/{chat_id}/multimodal-operations
POST /v1/attachments/{attachment_id}/vision
POST /v1/attachments/{attachment_id}/transcribe
POST /v1/chats/{chat_id}/speech
POST /v1/chats/{chat_id}/research
```

Attachment-to-message linking is fenced by chat. An attachment from Chat A cannot be attached to a message in Chat B.

Multimodal operation inputs/outputs are likewise checked against the same chat boundary.

## Web UX

The existing Workspace page is augmented rather than replaced.

The composer area gains:

- `📎 Arquivo`;
- `🎙` microphone recording;
- `🌐 Pesquisar`;
- pending attachment chips.

Messages can render:

- image previews;
- audio controls;
- file links;
- explicit `Analisar imagem` action;
- explicit `Transcrever` action;
- `🔊 Ouvir` on internal Amodex messages.

User-controlled message text is not interpolated into inline TTS event-handler source. The `Ouvir` action is bound with a DOM event listener.

## Failure semantics

Every provider-backed operation is first persisted as `RUNNING` and then moves to one terminal state:

```text
SUCCEEDED
FAILED
BLOCKED
```

Examples of `BLOCKED`:

- provider capability not configured;
- incomplete provider configuration;
- billing class not explicitly free.

There is no automatic paid fallback and no blind retry loop for side-effecting actions.

## Step 4 boundary

Step 3 intentionally does not import or own model-routing authority.

The status endpoint exposes:

```text
automatic_model_routing = GATED_STEP_4
execution_authority = ENVIRONMENT_ONLY
```

Step 4 remains responsible for any future model benchmark, ranking, promotion/demotion and automatic capability routing.

## Checkpoint validation contract

Step 3 is not valid unless the exact candidate SHA proves:

- Python compile succeeds in the isolated runtime;
- Step 3 does not import the Hermes worker implementation;
- PostgreSQL migration succeeds;
- binary upload/readback works;
- upload size is bounded;
- PostgreSQL contains no raw attachment `BYTEA` payload;
- message/attachment cross-chat binding is rejected;
- image analysis is an explicit audited operation;
- public `AMODEX` message impersonation remains rejected;
- STT returns text without auto-sending an OWNER message;
- TTS output is stored content-addressed;
- research records source provenance and does not fetch result pages;
- non-free model-backed capability is blocked before provider invocation;
- media store permissions/content digest/traversal guards work;
- Step 1 and Step 2 regressions remain green;
- Environment Product and Stage 6 regressions remain green;
- Step 4 routing/lifecycle code is absent from the Step 3 implementation;
- repository hygiene remains clean.

## External setup boundary

CI uses deterministic injected fake multimodal clients. It does not claim the owner's real Groq/OpenRouter/NVIDIA/SearXNG credentials or services are configured, reachable or currently free.

A live deployment must separately configure the desired zero-cost provider accounts, secret environment variables, media storage path and SearXNG endpoint.

The code must continue to fail closed if a required capability is missing or its billing policy is not explicitly free.
