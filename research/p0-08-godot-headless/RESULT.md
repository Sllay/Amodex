# P0-08 — Godot 4.7.2 Headless Runner

Status: **ADAPT**

## Decision

The Black-Islands-like Godot headless toolchain is technically accepted on a disposable zero-cost public GitHub Actions runner. The exact pinned engine/toolchain can be installed, validated, exercised through LSP and Godot MCP, and used for real headless project execution.

The decision is **ADAPT**, not unconditional ADOPT, because the proof used an isolated Amodex fixture in a public repository. It does not claim unlimited zero-cost execution for the private Black-Islands repository; P0-07's backend-eligibility rule still applies.

No Black-Islands file was modified.

## Revalidated Black-Islands pins

Read-only inspection of `Sllay/Black-Islands` confirmed the current project pins used by this PoC:

```text
Godot       4.7.2
Node.js     22.23.4
Godot MCP   0.1.2
Serena      2.1.0
```

Godot Linux x86_64 artifact:

```text
https://github.com/godotengine/godot-builds/releases/download/4.7.2-stable/Godot_v4.7.2-stable_linux.x86_64.zip
sha256: cadd3204e728a35d3f13adb7fd0d7902636b79f6b95c40c265eb73b6c35329e4
```

Node Linux x64 artifact used official release integrity:

```text
node-v22.23.4-linux-x64.tar.xz
sha256: 1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce
```

## Successful reinforced workflow

```text
workflow: stage0-godot-headless
run:      41198553847
job:      124529570541
source:   9b172134a3d14e7ca48948ae9e408608561718fc
runner:   GitHub hosted Ubuntu 26.04.1
```

All workflow steps completed successfully.

## Evidence

### Exact binaries / packages

```text
Godot:     4.7.2.stable.official.ed1daf0bf
Node:      v22.23.4
npm/npx:   10.9.9
Serena:    2.1.0
Godot MCP: 0.1.2
```

Both the Godot and Node downloads passed strict SHA-256 validation before execution.

### Serena / Godot LSP compatibility

The Runner started Godot exactly in the Black-Islands-style headless editor/LSP shape:

```text
Godot --headless --editor --lsp-port 6008 -- --godot-mcp-lsp-port=6008
```

Observed:

```text
GODOT_LSP_6008_OK
```

The port accepted a real TCP connection and the editor/LSP log contained no `SCRIPT ERROR` or `Parse Error` diagnostics.

The earlier cross-step `wait` warning was removed; shutdown now sends the signal and polls process liveness without trying to `wait` for a PID owned by a previous Actions shell.

### Parser/import and real project boot

The fixture was imported by a real Godot editor process and then executed by the real engine.

Runtime proof:

```text
AMODEX_P0_08_BOOT_OK value=42
```

Focused deterministic headless test:

```text
AMODEX_P0_08_HEADLESS_TEST_OK value=42
```

### Real MCP protocol

The probe starts `@shin2344234/godot-mcp@0.1.2` over stdio and speaks MCP JSON-RPC directly.

Observed:

```text
protocol_version: 2025-06-18
godot_status_present: true
godot_status_call_succeeded: true
server_version: 0.1.2
godot_version: 4.7.2.stable.official.ed1daf0bf
read_only: true
```

### Addon-backed headless scene backend

The first successful P0-08 run proved the MCP server and Godot connection but intentionally had no addon installed, so `scene_backend` was unavailable. The reinforced run closed that gap.

The workflow copies the fixture into `$RUNNER_TEMP`, then runs the exact pinned CLI:

```text
npx -y @shin2344234/godot-mcp@0.1.2 init
```

The temporary runtime copy receives `addons/godot_mcp`, the plugin is enabled, and repository-tracked fixture files remain untouched.

Final `godot_status`:

```text
addon_installed: true
editor_connected: false
scene_backend: "headless (edits .tscn files on disk)"
project_found: true
project_name: "Amodex P0-08 Headless Fixture"
server_version: "0.1.2"
godot.version: "4.7.2.stable.official.ed1daf0bf"
```

This proves the MCP headless scene path itself, not merely server startup.

## Resource snapshot

Final runner snapshot after the proof:

```text
RAM total:       15,983 MiB
RAM used:         1,246 MiB
RAM available:   14,736 MiB
swap used:             0 MiB
root disk free:       87 GiB
```

Tool directory sizes:

```text
Godot:        140 MiB
Node:         203 MiB
Serena venv:  140 MiB
MCP fixture:  1.4 MiB
```

These are hosted-runner observations, not production minimum requirements or peak-memory guarantees.

## Safety / integrity

- Black-Islands was read only.
- Addon installation occurred only in `$RUNNER_TEMP` fixture copy.
- No public editor bridge was exposed.
- MCP used stdio locally.
- LSP listened only inside the disposable Runner environment.
- Project/tool pins were not upgraded.
- `git diff --exit-code` confirmed no runtime-generated modification of tracked P0-08 fixture/workflow content.

## Frozen direction

```text
GodotHeadlessRunner profile
  -> exact project pins
  -> Godot headless editor/LSP
  -> Serena-compatible LSP endpoint
  -> Godot MCP local stdio
  -> addon-backed headless scene backend
  -> parser/import
  -> real runtime boot
  -> focused headless tests
  -> evidence/log collection
```

The profile is accepted. RunnerBackend selection remains cost/account-aware and backend-neutral.

## Final decision

**ADAPT.**

Godot 4.7.2 headless support is proven for a Black-Islands-like pinned toolchain. The remaining adaptation is infrastructure/account policy for private/heavy workloads, not a Godot/MCP feasibility gap.
