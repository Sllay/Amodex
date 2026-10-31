# P0-07 — General Runner

Status: **ADAPT**

## Decision

GitHub Actions standard hosted runners are accepted as the first disposable General Runner backend for public-repository Stage 0/Stage 1 work, with an explicit limitation: private repositories consume plan minutes and therefore this backend is not treated as unlimited zero-cost compute for Black-Islands.

The Runner contract remains backend-neutral. GitHub Actions is an initial RunnerBackend implementation candidate, not a permanent architectural dependency.

## Normal lifecycle evidence

Workflow:

```text
stage0-general-runner
run: 41190734514
```

Three separate hosted jobs were used:

```text
runner-a  -> success
runner-b  -> success
audit     -> success
```

Proven:

- fresh disposable VM starts clean;
- opaque task/runner identity is generated;
- exact Stage 0 source is checked out;
- deterministic task executes;
- external heartbeat/status is emitted;
- SAFE checkpoint is committed to a temporary remote branch;
- Runner A terminates;
- Runner B starts in a different VM and reconstructs from the remote checkpoint;
- local filesystem from Runner A is not required by Runner B;
- audit validates external heartbeat/evidence;
- temporary checkpoint ref is removed after validation.

## External cancellation evidence

Target workflow:

```text
stage0-general-runner-cancel
run: 41197666081
job: 124527007955
source SHA: aa25cc97c90f7faa1dbd196debc649ec12aa9df7
```

Controller workflow:

```text
stage0-general-runner-cancel-controller
run: 41197666080
job: 124527008090
same source SHA: aa25cc97c90f7faa1dbd196debc649ec12aa9df7
```

Observed sequence:

1. target Runner starts;
2. target pushes `.amodex-p007/cancel-checkpoint.json` to `p0-07-cancel-<run>-1`;
3. target enters long-running execution;
4. independent controller workflow discovers the exact target run by source SHA;
5. controller waits until the SAFE checkpoint branch exists remotely;
6. controller calls GitHub Actions cancel endpoint for that exact run;
7. target concludes `cancelled` while the checkpoint step remains successful;
8. controller fetches the remote checkpoint after cancellation;
9. controller validates `state=SAFE_BEFORE_CANCEL`, task identity and boot fingerprint;
10. controller deletes the temporary branch;
11. branch audit confirms no `p0-07-cancel-*` refs remain.

Target job result:

```text
Push SAFE checkpoint before long execution -> success
Enter cancellable wait after durable checkpoint -> cancelled
job conclusion -> cancelled
```

Controller result:

```text
Discover target run, wait for SAFE checkpoint, cancel externally, reconstruct, cleanup -> success
job conclusion -> success
```

This proves the required invariant:

```text
Control Plane authority
      -> cancel RunnerBackend execution
      -> Runner process disappears
      -> durable checkpoint survives
      -> replacement process can reconstruct
```

## Earlier timeout-only run

An earlier run (`41190797006`) also showed that a checkpoint survives Runner death, but it was terminated by the workflow timeout rather than an external controller. It is retained as supporting fault evidence only and is not used as the external-cancel proof.

## Cost / backend limitation

Current GitHub Actions policy was revalidated before this PoC:

- standard GitHub-hosted runners are free for public repositories;
- private repositories consume account/plan included minutes and may incur billing if paid usage is enabled.

Amodex zero-cost policy therefore requires the Provider/Runner Governor to classify backend eligibility per repository/account before dispatch. It must never silently consume paid Actions capacity.

## Frozen direction

```text
RunnerBackend interface
        |
        +-- GitHubActionsRunnerBackend   (accepted initial/public-repo backend)
        +-- future free backend(s)       (for private/heavy workloads)
```

No durable truth may live only on the hosted Runner filesystem.

## Final decision

**ADAPT.**

The lifecycle model is accepted. GitHub Actions is suitable as an initial disposable General Runner where the zero-cost eligibility check passes, but Amodex must preserve backend portability and cannot assume unlimited free execution for private repositories.
