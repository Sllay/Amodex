# P0-09 — Godot Visual Runner

Status: **ADAPT**

## Decision

A zero-cost public GitHub Actions Runner can execute a real Godot 4.7.2 windowed runtime under Xvfb/software rendering, control it through the pinned Godot MCP runtime bridge, inject native input, capture screenshots, collect runtime/performance evidence, repeat the procedure deterministically, upload artifacts, and terminate without orphaned Godot/display processes.

The decision is **ADAPT**, not unconditional ADOPT, for the same reason as P0-07/P0-08: this proves the visual/runtime capability on a public-repository free backend. It does not claim unlimited zero-cost private-repository minutes for Black-Islands.

No Black-Islands file was modified.

## Successful workflow

```text
workflow: stage0-godot-visual
run:      41199309448
job:      124531754855
source:   1c016b7693bd94658562e089b2b493018e68878a
runner:   GitHub hosted Ubuntu 26.04.1
```

All visual workflow steps completed successfully.

## Toolchain / display

```text
Godot:           4.7.2.stable.official.ed1daf0bf
Node:            v22.23.4
npm/npx:         10.9.9
Godot MCP:       0.1.2
virtual display: Xvfb :99
X display:       1280x720x24
MCP protocol:    2025-06-18
render method:   gl_compatibility
Godot display:   X11
```

Software OpenGL diagnostics:

```text
direct rendering: Yes
OpenGL vendor:   Mesa
OpenGL renderer: llvmpipe (LLVM 21.1.8, 256 bits)
OpenGL version:  4.5 Compatibility Profile / Mesa 26.0.8
```

Godot and Node downloads were validated against the same exact SHA-256 pins used by the P0-08 Black-Islands-like toolchain proof.

## Live runtime proof

The Godot MCP addon was installed only into a `$RUNNER_TEMP` copy of the P0-09 fixture. The MCP server then started the actual windowed game with a loopback remote-debug connection.

The fixture initially rendered:

```text
AMODEX VISUAL RUNNER
WAITING_INPUT
CLICK_ME
```

The probe discovered the button using `godot_game_tree`, then injected a native MCP click at the discovered node path.

Observed first iteration input evidence:

```text
button path: Panel/ClickButton
click coordinate: [320, 260]
handled: true
hit: Panel/ClickButton
hit_target: true
viewport: [640, 360]
input elapsed: 93 ms
```

After input, the live Godot state changed to:

```text
INPUT_OK
CLICKED
```

The fixture runtime also emitted both required log markers:

```text
AMODEX_P0_09_WINDOWED_READY
AMODEX_P0_09_INPUT_OK
```

No `SCRIPT ERROR` or `Parse Error` was accepted by the probe.

## Screenshot evidence

The complete proof was repeated twice. All screenshots were real MCP image blocks decoded as PNG and independently re-opened after downloading the workflow artifact.

Dimensions for all four screenshots:

```text
640 x 360 RGBA
```

Stable before hash across both iterations:

```text
6cdead7a335728699d845db86c508825c0004eaccd114a8c3878da9085035857
```

Stable after hash across both iterations:

```text
679669093321fe835cc70a4c111fcf03d18a95725b6d89ad50c06da928e3c9d6
```

MCP image comparison reported:

```text
changed_percent: 98.51
```

Manual artifact inspection confirmed the actual visual transition:

```text
before:
  dark background
  WAITING_INPUT
  CLICK_ME

after:
  teal/green background
  INPUT_OK
  CLICKED
```

This closes the visual-evidence gate with image bytes, not only textual assertions.

## Timing / repeatability

Iteration 1:

```text
startup:           2707.13 ms
before screenshot:   44.07 ms
after screenshot:    84.56 ms
input:                93 ms
sample avg FPS:      367.8
sample frame avg:      2.719 ms
```

Iteration 2:

```text
startup:            642.05 ms
before screenshot:   43.73 ms
after screenshot:    84.32 ms
input:               100 ms
sample avg FPS:      371.1
sample frame avg:      2.695 ms
```

The screenshots/hashes and 98.51% diff were identical across the repeated runs, which is sufficient repeatability for this fixture.

## Runtime measurements

Probe-observed peak across Godot processes:

```text
peak RSS:              298.08 MiB
peak CPU percent sum:  190.0
```

Representative engine monitor values:

```text
nodes:              9
orphan_nodes:       0
static memory:      51.49 MiB
static max:         69.18 MiB
video memory:        8.44 MiB
texture memory:      2.33 MiB
buffer memory:       6.12 MiB
draw calls/frame:    6
pipeline compiles:   0 in all reported categories
```

Hosted-runner snapshot after the visual proof:

```text
RAM total:      15,983 MiB
RAM used:        1,116 MiB
RAM available:  14,866 MiB
swap used:            0 MiB
```

These measurements describe this hosted test environment only; they are not Black-Islands performance requirements or production capacity guarantees.

## Artifact evidence

GitHub Actions artifact:

```text
name:   p0-09-visual-evidence-41199309448
id:     13042370464
size:   66,610 bytes
zip sha256: deca0f55f2dd36fc8a706e0630fdff63daa4d610b86baf3fcea84bce229e81e8
expires: 2026-11-07T12:02:29Z
```

Contents independently inspected:

```text
before-1.png
after-1.png
before-2.png
after-2.png
evidence.json
sha256sums.txt
```

The artifact is temporary CI evidence; hashes and conclusion remain permanently recorded here.

## Cleanup / safety

The successful run proved:

```text
NO_GODOT_ORPHANS
P0-09 CLEANUP_OK
```

Additional safety constraints:

- Xvfb used `-nolisten tcp`;
- the Godot MCP bridge used loopback remote-debug ports;
- no public editor/bridge endpoint was exposed;
- addon installation happened only in the disposable runtime copy;
- repository integrity check passed;
- Black-Islands stayed read-only;
- exact Godot/Node/MCP pins were preserved.

## Failure observed and corrected

The first visual run (`41199184239`) failed before visual execution because `node --version` was checked in the same shell step where `$GITHUB_PATH` had just been written. GitHub Actions applies that path update only to following steps.

The fix explicitly executed `$NODE_HOME/bin/node`, `$NODE_HOME/bin/npm`, and `$NODE_HOME/bin/npx` inside the installation step. No pass criterion was weakened.

## Frozen direction

```text
GodotVisualRunner profile
  -> disposable RunnerBackend
  -> virtual display (Xvfb/equivalent)
  -> software renderer fallback (Mesa/llvmpipe accepted)
  -> exact project pins
  -> local Godot MCP bridge
  -> live game tree/state inspection
  -> native MCP input
  -> before/after screenshots + hashes
  -> performance/runtime evidence
  -> artifact upload
  -> strict process/display cleanup
```

## P0-10 relationship

P0-09 proves visual execution on the currently accepted GitHub Actions RunnerBackend. It does **not** prove RunnerBackend portability.

Per `docs/POC_REGISTER.md`, P0-10 is blocking only before Amodex claims Runner portability and may execute during Stage 1/2. Therefore this P0-09 result does not silently mark P0-10 complete.

## Final decision

**ADAPT.**

The visual-validation capability itself is proven. Remaining constraints are backend/account/cost portability constraints, not Godot visual/MCP feasibility gaps.
