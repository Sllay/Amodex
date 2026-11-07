"""Bounded Run lease renewal for the synchronous Stage1Worker (not Runner Job heartbeat)."""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from threading import Event, RLock, Thread
from typing import Callable, Self, TypeVar

from amodex.storage.postgres import PostgresRunRepository, StaleLeaseError

_LOG = logging.getLogger(__name__)
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class RunLeasePolicy:
    lease_seconds: int = 30
    renewal_interval_seconds: float = 10

    def __post_init__(self) -> None:
        if type(self.lease_seconds) is not int or not 3 <= self.lease_seconds <= 300:
            raise ValueError("Run lease duration must be an integer between 3 and 300 seconds")
        interval = self.renewal_interval_seconds
        if (type(interval) not in {int, float} or not math.isfinite(interval)
                or not 0.1 <= interval <= min(60, self.lease_seconds / 3)):
            raise ValueError("Run renewal interval must be 0.1..60 seconds and at most lease/3")

    @classmethod
    def from_environment(cls) -> Self:
        return cls(
            lease_seconds=int(os.environ.get("AMODEX_RUN_LEASE_SECONDS", "30")),
            renewal_interval_seconds=float(os.environ.get("AMODEX_RUN_LEASE_RENEW_INTERVAL_SECONDS", "10")),
        )


class RunLeaseRenewal:
    """First renewal failure permanently blocks this worker's subsequent writes/effects.

    Foreground writes and renewal share a local lock, never held during inference.
    The repository still enforces DB authority independently of this coordination.
    """

    def __init__(
        self, repository: PostgresRunRepository, *, run_id: str, worker_id: str,
        fencing_token: int, policy: RunLeasePolicy,
    ) -> None:
        self.repository = repository
        self.run_id = run_id
        self.worker_id = worker_id
        self.fencing_token = fencing_token
        self.policy = policy
        self._stop = Event()
        self._lock = RLock()
        self._failure_reason: str | None = None
        self._thread = Thread(target=self._run, name=f"run-lease-{run_id}", daemon=True)

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        # Renewal uses its own connection with connect, lock, statement and TCP timeouts.
        # Join before returning: there is no renewal left running after execution ends.
        self._thread.join()

    def check(self) -> None:
        with self._lock:
            if self._failure_reason is not None:
                raise StaleLeaseError("Run renewal failed; authority cannot be assumed", reason=self._failure_reason)

    def call(self, operation: Callable[..., _T], **kwargs: object) -> _T:
        with self._lock:
            self.check()
            result = operation(**kwargs)
            if kwargs.get("release_lease") is True:
                # Stop under the same lock so legitimate completion is not seen as renewal loss.
                self._stop.set()
            return result

    def renew_once(self) -> None:
        with self._lock:
            if self._stop.is_set():
                return
            try:
                self.repository.renew_lease(
                    run_id=self.run_id, worker_id=self.worker_id,
                    fencing_token=self.fencing_token, lease_seconds=self.policy.lease_seconds,
                )
            except Exception as exc:
                self._failure_reason = exc.reason if isinstance(exc, StaleLeaseError) else "RENEWAL_UNCONFIRMED"
                self._stop.set()
                # Never log exception text: connection errors may contain credentials/DSNs.
                _LOG.warning("Run renewal stopped: run=%s reason=%s error_type=%s",
                             self.run_id, self._failure_reason, type(exc).__name__)

    def _run(self) -> None:
        while not self._stop.wait(self.policy.renewal_interval_seconds):
            self.renew_once()
