# P0-01 — Executable Runtime Contracts

Status: **ADOPT**

P0-01 is the first completed Stage 0 feasibility gate. It proves that Amodex can represent authority-bearing runtime objects as strict, immutable, versioned Python contracts with reproducible dependency resolution and executable contract tests.

## Hypothesis

Amodex can represent authority-bearing runtime objects as strict, immutable, versioned Pydantic models without duplicating semantics across API/storage/worker boundaries.

## Implemented subset

- `ContractModel` with `extra="forbid"`, frozen snapshots and revalidated `evolve()` updates;
- RFC 9562 UUIDv7-compatible ID generation/validation using stdlib + `secrets`;
- deterministic canonical JSON serialization;
- schema-v1 rejection of future schema values;
- representative `TaskPackage`;
- `WorkerLease` with positive fencing token and expiry rules;
- `ToolCall` with side-effect classification, idempotency and expected-revision rules;
- `Checkpoint` with `WIP` / `SAFE` / `VALIDATED` trust classes;
- `ValidationEvidence` bound to an exact commit SHA;
- small `amodex-contract` validation/schema CLI surface.

## Local semantic validation

Initial development environment:

- Python `3.13.5`;
- Pydantic `2.13.5`;
- pytest `9.0.2`;
- uv `0.10.0`.

Executed:

```bash
PYTHONPATH=src pytest -q
```

Observed:

```text
..............                                                           [100%]
14 passed
```

The module CLI was also executed directly:

```bash
PYTHONPATH=src python -m amodex.contracts schema tool-call
```

and emitted the generated JSON Schema for `ToolCall`.

## Reproducibility gate — GitHub Actions

The local implementation container could not resolve `pypi.org`, so the dependency-lock gate was moved to a clean network-enabled GitHub-hosted runner rather than fabricating a lock.

Successful run:

- workflow: `stage0-contracts`;
- run ID: `41177645815`;
- event: `push`;
- result: `success`;
- runner OS: Ubuntu `26.04.1`;
- runner image: `ubuntu-26.04`, image version `20261025.179.1`;
- checkout action pinned to commit `11d5960a326750d5838078e36cf38b85af677262`;
- uv: `0.10.0`;
- Python installed by uv: `3.13.5`.

The run executed, in order:

```bash
uv lock --python 3.13.5
uv sync --python 3.13.5 --extra dev --frozen
uv run --python 3.13.5 --extra dev --frozen pytest -q
uv run --python 3.13.5 --extra dev --frozen python -m amodex.contracts schema tool-call
```

Observed dependency resolution:

```text
Resolved 12 packages
```

Observed clean frozen-environment test result:

```text
..............                                                           [100%]
14 passed
```

Observed generated lock SHA-256:

```text
b0c4eb6f74a569e98957e27916e206f01d69f173f26b92db5184c58d2b91470c  uv.lock
```

The CI committed the generated lock back to `stage/0-feasibility` as commit:

```text
5aeee1fa235818b460fcba213d6b496e3f1107e6
chore(stage0): lock contract dependencies
```

`uv.lock` is now tracked and records the Python range `>=3.13,<3.15`, the direct pins from `pyproject.toml`, and exact resolved package artifacts/hashes.

## Proven invariants

The 14-test suite covers the representative contract semantics required by the P0-01 gate:

1. UUIDv7 generation/validation;
2. deterministic JSON round-trip;
3. unknown authority field rejection;
4. future schema-version rejection;
5. frozen snapshots;
6. immutable identity during controlled evolution;
7. enum rejection;
8. write tool calls require idempotency keys;
9. conditionally idempotent calls require expected revision;
10. `VALIDATED` checkpoint requires evidence references;
11. validation evidence remains bound to exact SHA;
12. lease fencing token must be positive;
13. lease expiry must be later than acquisition;
14. exact protected/write scope overlap is rejected;
15. CLI valid/invalid payload paths are included within the 14-test suite.

## Known boundary

This PoC intentionally implements only a representative contract subset. It does **not** claim that every object in `docs/contracts/RUNTIME_CONTRACTS.md` has already been implemented. Additional contracts enter production only when the vertical slice that consumes them begins.

The pull-request-triggered workflow generated after the bot lock commit reported `action_required`; the authoritative reproducibility evidence for P0-01 is the successful push workflow above. Future CI policy will be refined without treating that unrelated approval state as a contract failure.

## Decision

**ADOPT.**

Use this contract strategy as the Stage 0/Stage 1 baseline:

- Pydantic v2 strict/frozen authority snapshots;
- JSON-compatible canonical interchange;
- UUIDv7 string identities;
- explicit schema versions;
- reject unknown authority fields;
- revalidated state evolution rather than in-place mutation;
- generated JSON Schema at external boundaries;
- `uv` lock-based reproducibility.

Architecture impact: none. The P0-01 result validates the Part 2 contract direction and unlocks P0-02 Hermes embedding/authority isolation.
