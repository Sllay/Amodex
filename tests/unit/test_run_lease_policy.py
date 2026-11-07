"""Run policy bounds and loss latch; no DB/provider/network needed."""
from __future__ import annotations

from threading import Event
from types import SimpleNamespace

import pytest

from amodex.runtime.run_lease import RunLeasePolicy, RunLeaseRenewal
from amodex.storage.postgres import StaleLeaseError


@pytest.mark.parametrize("duration,interval", [
    (2, 0.1), (301, 1), (True, 0.1), (30.0, 1),
    (30, 0), (30, 0.09), (30, 11), (300, 61),
    (30, float("inf")), (30, float("nan")), (30, True), (30, "1"),
])
def test_unsafe_policy_values_are_rejected(duration, interval):
    with pytest.raises(ValueError):
        RunLeasePolicy(duration, interval)


@pytest.mark.parametrize("duration,interval", [(3, 0.1), (3, 1), (30, 10), (300, 60)])
def test_policy_accepts_bounded_duration_and_interval(duration, interval):
    assert RunLeasePolicy(duration, interval).lease_seconds == duration


def test_environment_configuration_is_separate_from_provider_timeout(monkeypatch):
    monkeypatch.setenv("AMODEX_RUN_LEASE_SECONDS", "90")
    monkeypatch.setenv("AMODEX_RUN_LEASE_RENEW_INTERVAL_SECONDS", "20")
    monkeypatch.setenv("AMODEX_PROVIDER_TIMEOUT_SECONDS", "1800")
    assert RunLeasePolicy.from_environment() == RunLeasePolicy(90, 20)


def test_first_transport_failure_is_latched_no_retry_or_new_effect(caplog):
    calls = []
    def fail(**kwargs):
        calls.append(kwargs)
        raise ConnectionError("secret-do-not-log")
    guard = RunLeaseRenewal(SimpleNamespace(renew_lease=fail), run_id="synthetic", worker_id="worker", fencing_token=4, policy=RunLeasePolicy())
    guard.renew_once()
    guard.renew_once()
    with pytest.raises(StaleLeaseError) as error:
        guard.call(lambda: pytest.fail("effect executed after loss"))
    assert error.value.reason == "RENEWAL_UNCONFIRMED"
    assert len(calls) == 1
    assert "secret-do-not-log" not in caplog.text


def test_context_cleanup_stops_thread_even_if_foreground_raises():
    calls = Event()
    guard = RunLeaseRenewal(SimpleNamespace(renew_lease=lambda **kwargs: calls.set()), run_id="synthetic", worker_id="worker", fencing_token=4, policy=RunLeasePolicy(3, 0.1))
    with pytest.raises(RuntimeError):
        with guard:
            assert calls.wait(5)
            raise RuntimeError("synthetic foreground failure")
    assert not guard._thread.is_alive()


@pytest.mark.parametrize("duration", [0, -1, 301, True, 1.5, "30"])
@pytest.mark.parametrize("operation", ["claim", "renew"])
def test_repository_rejects_invalid_duration_before_connecting(duration, operation):
    from amodex.storage.postgres import PostgresRunRepository
    repo = PostgresRunRepository("must-not-connect")
    with pytest.raises(ValueError, match="lease_seconds"):
        if operation == "claim":
            repo.claim_next(worker_id="synthetic", lease_seconds=duration)
        else:
            repo.renew_lease(run_id="synthetic", worker_id="synthetic", fencing_token=1, lease_seconds=duration)
