# P0-01 — Executable Runtime Contracts

Status: **IN_PROGRESS**

This record captures the first real Stage 0 implementation evidence. It is intentionally not marked `ADOPT` yet because dependency-lock reproduction still requires a network-enabled runner.

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

## Environment actually exercised

- Python: `3.13.5`
- Pydantic: `2.13.5`
- pytest: `9.0.2`
- uv executable available: `0.10.0`
- execution environment: Linux container used for Stage 0 development

The current `pyproject.toml` pins the directly exercised Pydantic/pytest versions. This is not a substitute for a transitive lock.

## Executed validation

Command:

```bash
PYTHONPATH=src pytest -q
```

Observed result:

```text
..............                                                           [100%]
14 passed
```

The suite proves:

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
15. CLI valid/invalid payload paths are covered within the 14-test suite.

The module CLI was also invoked directly:

```bash
PYTHONPATH=src python -m amodex.contracts schema tool-call
```

and emitted the generated JSON Schema for `ToolCall`.

## Dependency-lock attempt

Attempted:

```bash
uv lock
```

Observed result: `uv` retried PyPI and failed because this execution environment could not resolve `pypi.org` (DNS/network unavailable).

This is an environment limitation, not evidence that the dependency graph is resolvable. Therefore no fabricated or hand-written `uv.lock` was committed.

## Remaining closure gate

Before P0-01 can become `ADOPT`:

1. run `uv lock` in a network-enabled trusted runner;
2. inspect the resolved dependency graph;
3. run a clean `uv sync --frozen --extra dev` from the generated lock;
4. rerun the full contract suite from that clean environment;
5. verify JSON Schema generation under the locked versions;
6. record the exact lock SHA and Python version used.

## Current conclusion

**RETEST / likely ADOPT.**

The contract strategy itself passed every executed semantic test. The only unresolved acceptance item is reproducible dependency locking/install from a clean network-enabled runner.

No other Amodex production subsystem depends on this PoC yet, so the unresolved lock cannot silently leak forward into Stage 1.
