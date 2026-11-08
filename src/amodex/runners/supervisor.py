"""Foreground POSIX supervisor for one runner operation; no reconnect or replay.

Each workflow step uses the same private state file. STARTED left by a crash and
any authority failure block later work. This is local safety state, not Recovery
or an external exactly-once contract. Work must not escape its process group.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass

from amodex.runners.client import AuthorityStatus, RunnerClientError, RunnerControlClient


@dataclass(frozen=True)
class SupervisionResult:
    status: str
    returncode: int | None = None
    authority: str | None = None
    outcome_unknown: bool = False


class RunnerSupervisor:
    def __init__(self, *, client: RunnerControlClient, job_id: str, github_run_id: str,
                 state_file: Path, interval_seconds: float = 20, grace_seconds: float = 2,
                 work_timeout_seconds: float = 1800, control_timeout_seconds: float = 5) -> None:
        for value, maximum in ((interval_seconds, 30), (grace_seconds, 10), (work_timeout_seconds, 1800),
                               (control_timeout_seconds, 10)):
            if not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError("invalid supervisor timing")
        self.client, self.job_id, self.owner = client, job_id, github_run_id
        self.path = state_file
        self.interval, self.grace, self.work_timeout = interval_seconds, grace_seconds, work_timeout_seconds
        self.control_timeout = control_timeout_seconds
        self._lease_deadline: float | None = None
        self._next_heartbeat = 0.0
        self._cancel = threading.Event()
        self._handlers: dict[int, object] = {}
        self._lock_file = None
        self._subreaper_previous: int | None = None
        self.state: dict = {}

    def __enter__(self):
        try:
            if threading.current_thread() is not threading.main_thread():
                raise ValueError("supervisor requires the foreground POSIX main thread")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._lock_file = open(str(self.path) + ".lock", "a")
            os.chmod(self._lock_file.name, 0o600)
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.state = {"job_id": self.job_id, "github_run_id": self.owner, "operations": {}}
            if self.path.exists():
                self.state = json.loads(self.path.read_text())
                if (self.state.get("job_id"), self.state.get("github_run_id")) != (self.job_id, self.owner):
                    raise ValueError("supervision identity mismatch")
                if any(v == "STARTED" for v in self.state["operations"].values()):
                    self.state.update(blocked=AuthorityStatus.AUTHORITY_UNCONFIRMED.value, work_stopped=True)
                    self._save()
            # Linux adopts orphaned descendants so controlled group shutdown can
            # reap them too. Other POSIX hosts still terminate the process group.
            if os.uname().sysname == "Linux":
                libc, previous = ctypes.CDLL(None, use_errno=True), ctypes.c_int()
                if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0 or libc.prctl(36, 1, 0, 0, 0) != 0:
                    raise OSError("subreaper unavailable")
                self._subreaper_previous = previous.value
            if threading.current_thread() is threading.main_thread():
                for sig in (signal.SIGTERM, signal.SIGINT):
                    self._handlers[sig] = signal.signal(sig, self.cancel)
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        for sig, handler in self._handlers.items():
            signal.signal(sig, handler)
        self._handlers.clear()
        if self._subreaper_previous is not None:
            ctypes.CDLL(None).prctl(36, self._subreaper_previous, 0, 0, 0)
            self._subreaper_previous = None
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None

    def _save(self) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as out:
                temporary = Path(out.name)
                json.dump(self.state, out, sort_keys=True)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def cancel(self, *_):
        self._cancel.set()

    def _record_cancel(self) -> None:
        self.state.update(cancelled=True, work_stopped=True)
        self._save()

    @contextmanager
    def _bounded_control(self):
        # Bound the whole request, not just each HTTP transport phase. This also
        # stops an inconclusive/slow response from outliving our known lease.
        timeout = self.control_timeout
        if self._lease_deadline is not None:
            timeout = min(timeout, self._lease_deadline - time.monotonic())
        if timeout <= 0:
            raise TimeoutError()
        def expired(*_):
            raise TimeoutError()
        old_handler = signal.signal(signal.SIGALRM, expired)
        old_timer = signal.setitimer(signal.ITIMER_REAL, timeout)
        started = time.monotonic()
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
            if old_timer[0]:
                signal.setitimer(signal.ITIMER_REAL, max(0.000001, old_timer[0] - (time.monotonic()-started)), old_timer[1])

    def _renew(self) -> bool:
        if self.state.get("blocked"):
            return False
        if self._lease_deadline is not None and time.monotonic() >= self._lease_deadline:
            self.state.update(blocked=AuthorityStatus.LEASE_LOST.value, work_stopped=True)
            self._save()
            return False
        try:
            with self._bounded_control():
                result = self.client.check_authority(job_id=self.job_id, github_run_id=self.owner)
            status = result.status
            if not isinstance(status, AuthorityStatus):
                raise ValueError("invalid authority result")
            if status == AuthorityStatus.HEALTHY:
                remaining = (result.lease_expires_at - datetime.now(timezone.utc)).total_seconds()
                if remaining <= 0:
                    status = AuthorityStatus.LEASE_LOST
                else:
                    self._lease_deadline = time.monotonic() + remaining
                    self._next_heartbeat = time.monotonic() + min(self.interval, remaining / 2)
        except Exception:
            status = AuthorityStatus.AUTHORITY_UNCONFIRMED
        if status != AuthorityStatus.HEALTHY:
            self.state.update(blocked=status.value, work_stopped=True)
            self._save()
            return False
        return True

    @staticmethod
    def _group_exists(group: int) -> bool:
        try:
            os.killpg(group, 0)
            return True
        except ProcessLookupError:
            return False

    @staticmethod
    def _signal_group(group: int, sig: int) -> None:
        try:
            os.killpg(group, sig)
        except ProcessLookupError:
            pass

    @staticmethod
    def _reap_group(group: int) -> None:
        while True:
            try:
                pid, _ = os.waitpid(-group, os.WNOHANG)
                if pid == 0:
                    break
            except ChildProcessError:
                break

    def _cleanup(self, process: subprocess.Popen) -> bool:
        group = process.pid
        self._signal_group(group, signal.SIGTERM)
        deadline = time.monotonic() + self.grace
        while self._group_exists(group) and time.monotonic() < deadline:
            if process.poll() is not None:
                self._reap_group(group)
            time.sleep(0.01)
        if self._group_exists(group):
            self._signal_group(group, signal.SIGKILL)
        process.wait(timeout=self.grace)
        deadline = time.monotonic() + self.grace
        while self._group_exists(group) and time.monotonic() < deadline:
            self._reap_group(group)
            time.sleep(0.01)
        return not self._group_exists(group)

    def run(self, argv: list[str], *, operation: str) -> SupervisionResult:
        if (self._lock_file is None or self.state.get("blocked") or self.state.get("work_stopped")
                or self.state.get("terminal_attempted") or operation in self.state["operations"]):
            return SupervisionResult("BLOCKED", authority=self.state.get("blocked"), outcome_unknown=True)
        if not self._renew():
            return SupervisionResult("BLOCKED", authority=self.state["blocked"])
        if self._cancel.is_set():
            self._record_cancel()
            return SupervisionResult("CANCELLED")
        self.state["operations"][operation] = "STARTED"
        self._save()  # Record BEFORE an effect; a crash never grants a replay.
        process = None
        result = SupervisionResult("START_FAILED")
        try:
            if self._lease_deadline is None or time.monotonic() >= self._lease_deadline:
                self._renew()
                result = SupervisionResult("BLOCKED", authority=self.state.get("blocked"))
                return result
            child_env = {k: v for k, v in os.environ.items() if k not in {
                "AMODEX_RUNNER_CONTROL_TOKEN", "AMODEX_CONTROL_PLANE_URL"}}
            process = subprocess.Popen(argv, start_new_session=True, env=child_env)
            end = time.monotonic() + self.work_timeout
            while True:
                if self._cancel.is_set():
                    self.state.update(cancelled=True, work_stopped=True)
                    result = SupervisionResult("CANCELLED", outcome_unknown=True)
                    break
                if time.monotonic() >= end:
                    result = SupervisionResult("WORK_TIMEOUT", outcome_unknown=True)
                    break
                if time.monotonic() >= self._next_heartbeat:
                    if not self._renew():
                        result = SupervisionResult("BLOCKED", authority=self.state["blocked"], outcome_unknown=True)
                        break
                code = process.poll()
                if code is not None:
                    if self._renew():
                        result = SupervisionResult("COMPLETE" if code == 0 else "WORK_FAILED", code,
                                                   outcome_unknown=code != 0)
                    else:
                        result = SupervisionResult("BLOCKED", code, self.state["blocked"], True)
                    if self._cancel.is_set():
                        self.state.update(cancelled=True, work_stopped=True)
                        result = SupervisionResult("CANCELLED", code, outcome_unknown=True)
                    break
                self._cancel.wait(min(0.05, self.interval, max(.001, self._next_heartbeat-time.monotonic())))
        except Exception:
            result = SupervisionResult("WORK_UNKNOWN" if process else "START_FAILED", outcome_unknown=process is not None)
        finally:
            if process is not None:
                try:
                    if not self._cleanup(process):
                        result = SupervisionResult("CLEANUP_FAILED", outcome_unknown=True)
                except Exception:
                    result = SupervisionResult("CLEANUP_FAILED", outcome_unknown=True)
            if self._cancel.is_set() and result.status not in {"BLOCKED", "CLEANUP_FAILED"}:
                self.state.update(cancelled=True, work_stopped=True)
                result = SupervisionResult("CANCELLED", result.returncode, outcome_unknown=process is not None)
            self.state["operations"][operation] = result.status
            self.state["outcome_unknown"] = self.state.get("outcome_unknown", False) or result.outcome_unknown
            if result.status != "COMPLETE":
                self.state["work_stopped"] = True
            self._save()
        return result

    def running(self) -> SupervisionResult:
        if (self._lock_file is None or self.state.get("blocked") or self.state.get("work_stopped")
                or self.state.get("terminal_attempted") or "mark-running" in self.state["operations"]):
            return SupervisionResult("BLOCKED", outcome_unknown=True)
        if not self._renew():
            return SupervisionResult("BLOCKED", authority=self.state.get("blocked"))
        if self._cancel.is_set():
            self._record_cancel()
            return SupervisionResult("CANCELLED")
        self.state["operations"]["mark-running"] = "STARTED"
        self._save()
        try:
            with self._bounded_control():
                receipt = self.client.running(job_id=self.job_id, github_run_id=self.owner)
            if (receipt.get("job_id"), receipt.get("github_run_id"), receipt.get("state")) != (self.job_id, self.owner, "RUNNING"):
                raise ValueError()
            self.state["operations"]["mark-running"] = "COMPLETE"
            self._save()
            if self._cancel.is_set():
                self._record_cancel()
                return SupervisionResult("CANCELLED")
            return SupervisionResult("COMPLETE")
        except Exception as exc:
            self.state["operations"]["mark-running"] = "UNKNOWN"
            classification = exc.status if isinstance(exc, RunnerClientError) else AuthorityStatus.AUTHORITY_UNCONFIRMED
            self.state.update(blocked=classification.value, work_stopped=True)
            self._save()
            return SupervisionResult("BLOCKED", authority=self.state["blocked"], outcome_unknown=True)

    def report(self, *, state: str, summary: str, result_commit: str | None = None,
               evidence: dict | None = None) -> SupervisionResult:
        if (self._lock_file is None or self.state.get("terminal_attempted") or self.state.get("blocked")
                or self.state.get("cancelled") and state != "CANCELLED"
                or self.state.get("work_stopped") and state == "COMPLETED"):
            return SupervisionResult("REPORT_BLOCKED", authority=self.state.get("blocked"), outcome_unknown=True)
        if state not in {"COMPLETED", "FAILED", "CANCELLED"} or not self._renew():
            return SupervisionResult("REPORT_BLOCKED", authority=self.state.get("blocked"))
        if self._cancel.is_set() and state != "CANCELLED":
            self._record_cancel()
            return SupervisionResult("CANCELLED")
        self.state["terminal_attempted"] = True
        self._save()
        try:
            with self._bounded_control():
                receipt = self.client.report(job_id=self.job_id, github_run_id=self.owner, state=state,
                                             summary=summary, result_commit=result_commit, evidence=evidence)
            if (receipt.get("job_id"), receipt.get("github_run_id"), receipt.get("state")) != (self.job_id, self.owner, state):
                raise ValueError("inconclusive receipt")
            return SupervisionResult("REPORTED", outcome_unknown=self.state.get("outcome_unknown", False))
        except RunnerClientError as exc:
            self.state.update(blocked=exc.status.value, work_stopped=True)
            self._save()
            return SupervisionResult("REPORT_UNKNOWN", authority=exc.status.value, outcome_unknown=True)
        except Exception:
            return SupervisionResult("REPORT_UNKNOWN", outcome_unknown=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--github-run-id", required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--operation")
    parser.add_argument("--interval", type=float, default=20)
    parser.add_argument("--grace", type=float, default=2)
    parser.add_argument("--report-state", choices=("COMPLETED", "FAILED", "CANCELLED"))
    parser.add_argument("--mark-running", action="store_true")
    parser.add_argument("--summary", default="Supervised runner finished")
    parser.add_argument("--result-commit-file", type=Path)
    parser.add_argument("--evidence-json", type=Path)
    args, command = parser.parse_known_args(argv)
    if command[:1] == ["--"]:
        command = command[1:]
    if sum((bool(args.report_state), bool(command), args.mark_running)) != 1 or command and not args.operation:
        parser.error("choose one command with --operation or one --report-state")
    try:
        client = RunnerControlClient(os.environ.get("AMODEX_CONTROL_PLANE_URL", ""),
                                     os.environ.get("AMODEX_RUNNER_CONTROL_TOKEN", ""), timeout_seconds=5)
        with RunnerSupervisor(client=client, job_id=args.job_id, github_run_id=args.github_run_id,
                              state_file=args.state_file, interval_seconds=args.interval, grace_seconds=args.grace) as supervisor:
            if args.report_state:
                commit = args.result_commit_file.read_text().strip() if args.result_commit_file else None
                evidence = json.loads(args.evidence_json.read_text()) if args.evidence_json else {}
                result = supervisor.report(state=args.report_state, summary=args.summary, result_commit=commit, evidence=evidence)
            elif args.mark_running:
                result = supervisor.running()
            else:
                result = supervisor.run(command, operation=args.operation)
            output = asdict(result)
            if result.status == "CANCELLED":
                output["cancellation_report"] = asdict(supervisor.report(state="CANCELLED", summary="Runner cancelled under supervision"))
            print(json.dumps(output, sort_keys=True))
            return 0 if result.status in {"COMPLETE", "REPORTED"} else 2
    except Exception:
        print(json.dumps({"status": "SUPERVISOR_BLOCKED", "authority": "AUTHORITY_UNCONFIRMED"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
