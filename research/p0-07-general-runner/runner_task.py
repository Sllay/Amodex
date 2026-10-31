#!/usr/bin/env python3
"""P0-07 deterministic General Runner lifecycle fixture.

Research-only. Phase A creates a deterministic remote-checkpoint payload.
Phase B runs in a separate fresh runner, reconstructs from that checkpoint and
proves no durable truth depended on phase A's local filesystem.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def boot_fingerprint() -> str:
    candidates = [
        Path("/proc/sys/kernel/random/boot_id"),
        Path("/etc/machine-id"),
    ]
    for candidate in candidates:
        try:
            raw = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw:
            return sha256_text(raw)
    return sha256_text(f"fallback:{platform.node()}:{uuid.uuid4()}")


def deterministic_digest(task_id: str, source_sha: str) -> str:
    return sha256_text(f"{task_id}:{source_sha}:amodex-p0-07")


def phase_a(args: argparse.Namespace) -> int:
    checkpoint = Path(args.checkpoint)
    digest = deterministic_digest(args.task_id, args.source_sha)
    sentinel = Path(args.sentinel)
    sentinel.write_text("phase-a-local-only\n", encoding="utf-8")

    payload = {
        "schema_version": 1,
        "task_id": args.task_id,
        "source_sha": args.source_sha,
        "result_digest": digest,
        "phase_a_boot_fingerprint": boot_fingerprint(),
        "phase_a_runner_nonce": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "checkpoint_kind": "P0-07-SAFE",
        "local_sentinel_path": str(sentinel),
    }
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "phase": "A",
        "status": "PASS",
        "task_id": args.task_id,
        "result_digest": digest,
        "boot_fingerprint": payload["phase_a_boot_fingerprint"],
        "checkpoint": str(checkpoint),
    }, sort_keys=True))
    return 0


def phase_b(args: argparse.Namespace) -> int:
    checkpoint = Path(args.checkpoint)
    if not checkpoint.is_file():
        raise SystemExit("remote checkpoint missing")
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))

    if payload.get("task_id") != args.task_id:
        raise SystemExit("task identity mismatch")
    if payload.get("source_sha") != args.source_sha:
        raise SystemExit("source SHA mismatch")

    expected = deterministic_digest(args.task_id, args.source_sha)
    if payload.get("result_digest") != expected:
        raise SystemExit("deterministic checkpoint digest mismatch")

    sentinel = Path(str(payload.get("local_sentinel_path") or ""))
    if sentinel.exists():
        raise SystemExit("phase A local filesystem leaked into phase B runner")

    current_boot = boot_fingerprint()
    if payload.get("phase_a_boot_fingerprint") == current_boot:
        raise SystemExit("phase B appears to share the phase A boot identity")

    result = {
        "schema_version": 1,
        "phase": "B",
        "status": "PASS",
        "task_id": args.task_id,
        "source_sha": args.source_sha,
        "result_digest": expected,
        "phase_a_boot_fingerprint": payload.get("phase_a_boot_fingerprint"),
        "phase_b_boot_fingerprint": current_boot,
        "fresh_runner_proven": True,
        "local_sentinel_absent": True,
        "remote_checkpoint_reconstructed": True,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="phase", required=True)

    a = subparsers.add_parser("phase-a")
    a.add_argument("--task-id", required=True)
    a.add_argument("--source-sha", required=True)
    a.add_argument("--checkpoint", required=True)
    a.add_argument("--sentinel", required=True)
    a.set_defaults(func=phase_a)

    b = subparsers.add_parser("phase-b")
    b.add_argument("--task-id", required=True)
    b.add_argument("--source-sha", required=True)
    b.add_argument("--checkpoint", required=True)
    b.add_argument("--output", required=True)
    b.set_defaults(func=phase_b)

    args = parser.parse_args()
    if len(args.source_sha) != 40:
        raise SystemExit("source SHA must be exact 40-character Git SHA")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
