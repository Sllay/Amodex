# Engine and editor integration boundary

Status: **Stage 4 architectural seam; engine-specific expansion remains future work**

Amodex core policy must not know how a particular game engine, editor, IDE, or
runtime exposes its tools. The core authorizes capabilities; integration adapters
map those capabilities onto a concrete transport such as MCP, CLI, LSP, or a
future Desktop Runner.

This keeps repository authority, scope, cost, validation, and Promotion Gate
rules independent from Godot, Roblox Studio, or any other authoring surface.

## Contract

The integration boundary lives in `src/amodex/integrations/`.

An `EngineIntegrationAdapter` detects a repository/editor profile and returns an
`EngineIntegrationProfile` containing:

- stable adapter ID;
- engine/editor identity and version when known;
- source languages;
- runtime root and project descriptor;
- concrete tool bindings;
- capability set derived from those bindings;
- project constraints and provenance paths.

The current capability vocabulary includes code read/edit, semantic navigation,
scene/data-model read/edit, runtime execution, playtest control, diagnostics,
console access, screenshot/vision, input, and asset insertion/generation.

A TaskPackage still controls which of those capabilities a worker actually gets.
Detection never grants authority by itself.

## Godot profile

Stage 4 includes a deterministic Godot profile detector because the mandatory
Black-Islands BI-0/BI-1 benchmark already requires Amodex to understand the
project's real Godot/Serena/Godot-MCP boundaries.

The detector reads repository authority instead of guessing from a `.gd` file:

- root `AGENTS.md`;
- declared runtime root;
- `project.godot` descriptor;
- pinned engine version when declared;
- Serena and Godot MCP launcher/contract evidence.

Serena is mapped to semantic code capabilities. Godot MCP is mapped to
scene/runtime/playtest/diagnostic capabilities. Neither tool receives promotion
or policy authority.

## Luau recognition is not Roblox Studio support

Repository ingestion recognizes `.luau` and `.lua` as source languages and can
index basic Luau symbols. This is deliberately separate from an engine adapter.

Being able to read or patch Luau does **not** imply that Amodex can safely operate
Roblox Studio. A Studio integration must separately prove its DataModel,
runtime, diagnostics, playtest, visual/input, persistence, and recovery paths.

## Future Roblox Studio adapter

Research snapshot: **2026-10-31**.

Roblox currently documents a built-in Roblox Studio MCP server. It communicates
with MCP clients over local stdio and can expose Studio operations such as script
read/edit/search, DataModel exploration, Luau execution, playtest control, and
console/runtime interaction. Roblox's official agent integration also documents
input and multiple-Studio workflows.

Primary references:

- <https://create.roblox.com/docs/studio/mcp>
- <https://github.com/Roblox/roblox-studio-agent-plugin>

The older standalone Roblox Rust MCP server is deprecated in favor of the
built-in Studio MCP server:

- <https://github.com/Roblox/studio-rust-mcp-server>

A future `RobloxStudioIntegrationAdapter` should therefore prefer the official
built-in server and map it to Amodex capabilities instead of giving the MCP
server direct worker or repository authority.

Likely architecture:

```text
Amodex Control Plane
        |
        v
TaskPackage / capability grant
        |
        v
Desktop Runner (Windows or macOS)
        |
        +--> Roblox Studio MCP (local stdio)
        |      +--> DataModel
        |      +--> Luau scripts
        |      +--> execute/playtest
        |      +--> console/diagnostics
        |      +--> input/visual surfaces when available
        |
        +--> Git/validation tools
        +--> Luau parser/type diagnostics
        +--> screenshot/evidence capture
```

Roblox Studio is a desktop application, so this profile is materially different
from a headless Godot Runner. The Desktop Runner must own process lifecycle and
local MCP transport; the remote Control Plane remains authoritative for tasks,
leases, capabilities, validation, and promotion.

### Luau validation requirements

The future adapter must not rely on regex editing or model confidence for Luau.
It should combine, where available:

- Studio's own parser/runtime diagnostics;
- Luau type analysis for typed code;
- deterministic script/DataModel inspection;
- playtest assertions;
- console error capture;
- visual evidence only as a complement to executable assertions.

Strict or gradually typed Luau repositories should preserve their configured type
mode and project conventions rather than being silently rewritten into another
style.

### Vision and interaction fallback

If the official Studio MCP surface does not expose a required screenshot or input
primitive for a particular workflow, the missing capability belongs in a
companion Desktop Runner adapter. It must not be simulated by claiming that text
console output proves a visual result.

Any third-party MCP/plugin bridge remains optional and lower-trust than the
built-in Roblox path. It must be pinned, sandboxed, capability-scoped, and
validated before production use.

## Stage boundary

Stage 4 only establishes and tests the generic integration seam plus the Godot
profile required by Black-Islands. It does **not** claim production Roblox Studio
support and does not start Stage 5 capability routing.

A production Roblox adapter belongs after the seven-stage baseline unless a
future normative ADR deliberately changes that order.
