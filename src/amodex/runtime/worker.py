"""Durable run worker with cooperative Stage 6 controls."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from amodex.contracts.ids import new_uuid7
from amodex.providers.base import ProviderClient, ProviderFailure
from amodex.providers.gemini import GeminiProvider
from amodex.providers.governor import AdmissionStatus, ProviderGovernor
from amodex.storage.postgres import PostgresRunRepository
from .models import EventKind, ProviderRequestState, RunRecord, RunState
from .run_lease import RunLeasePolicy, RunLeaseRenewal


@dataclass(slots=True)
class Stage1Worker:
    repository: PostgresRunRepository
    provider: ProviderClient
    governor: ProviderGovernor
    worker_id: str
    lease_policy: RunLeasePolicy = field(default_factory=RunLeasePolicy)

    def _apply_control_if_requested(self, run: RunRecord, renewal: RunLeaseRenewal) -> RunRecord | None:
        desired = self.repository.get_run_control_state(run.run_id)
        if desired == "PAUSED":
            return renewal.call(self.repository.transition,
                run_id=run.run_id,
                worker_id=self.worker_id,
                fencing_token=run.fencing_token,
                state=RunState.PAUSED,
                kind=EventKind.RUN_PAUSED,
                payload={"reason": "durable owner pause applied before next provider side effect"},
                release_lease=True,
            )
        if desired == "CANCELLED":
            return renewal.call(self.repository.transition,
                run_id=run.run_id,
                worker_id=self.worker_id,
                fencing_token=run.fencing_token,
                state=RunState.CANCELLED,
                kind=EventKind.RUN_CANCELLED,
                payload={"reason": "durable owner cancellation applied before next provider side effect"},
                release_lease=True,
            )
        return None

    def process_once(self, *, free_quota_available: bool = True) -> RunRecord | None:
        run = self.repository.claim_next(
            worker_id=self.worker_id, lease_seconds=self.lease_policy.lease_seconds,
        )
        if run is None:
            return None

        with RunLeaseRenewal(
            self.repository, run_id=run.run_id, worker_id=self.worker_id,
            fencing_token=run.fencing_token, policy=self.lease_policy,
        ) as renewal:
            return self._process_claimed(run, renewal, free_quota_available=free_quota_available)

    def _process_claimed(
        self, run: RunRecord, renewal: RunLeaseRenewal, *, free_quota_available: bool,
    ) -> RunRecord:
        controlled = self._apply_control_if_requested(run, renewal)
        if controlled is not None:
            return controlled

        if run.provider_request_state == ProviderRequestState.STARTED:
            return renewal.call(self.repository.transition,
                run_id=run.run_id,
                worker_id=self.worker_id,
                fencing_token=run.fencing_token,
                state=RunState.UNKNOWN_OUTCOME,
                kind=EventKind.PROVIDER_OUTCOME_UNKNOWN,
                payload={
                    "request_key": run.provider_request_key,
                    "reason": "expired lease after provider request started; duplicate call forbidden",
                },
                last_error_code="PROVIDER_OUTCOME_AMBIGUOUS",
                release_lease=True,
            )

        if run.provider_request_state == ProviderRequestState.COMPLETED:
            if not run.result_text:
                return renewal.call(self.repository.transition,
                    run_id=run.run_id,
                    worker_id=self.worker_id,
                    fencing_token=run.fencing_token,
                    state=RunState.BLOCKED,
                    kind=EventKind.RUN_FAILED,
                    payload={"reason": "durable provider completion missing result payload"},
                    last_error_code="PROVIDER_COMPLETION_CORRUPT",
                    release_lease=True,
                )
            return renewal.call(self.repository.transition,
                run_id=run.run_id,
                worker_id=self.worker_id,
                fencing_token=run.fencing_token,
                state=RunState.COMPLETED,
                kind=EventKind.RUN_COMPLETED,
                payload={
                    "provider": run.provider,
                    "model": run.model,
                    "recovered_from": "durable_provider_completion",
                },
                release_lease=True,
            )

        decision = self.governor.admit(free_quota_available=free_quota_available)
        if decision.status == AdmissionStatus.WAITING_PROVIDER:
            return renewal.call(self.repository.transition,
                run_id=run.run_id,
                worker_id=self.worker_id,
                fencing_token=run.fencing_token,
                state=RunState.WAITING_PROVIDER,
                kind=EventKind.PROVIDER_WAIT,
                payload={"reason": decision.reason},
                last_error_code="FREE_QUOTA_UNAVAILABLE",
                release_lease=True,
            )
        if decision.status != AdmissionStatus.ADMITTED or decision.route is None:
            return renewal.call(self.repository.transition,
                run_id=run.run_id,
                worker_id=self.worker_id,
                fencing_token=run.fencing_token,
                state=RunState.BLOCKED,
                kind=EventKind.RUN_FAILED,
                payload={"reason": decision.reason},
                last_error_code="NO_ZERO_COST_ROUTE",
                release_lease=True,
            )

        route = decision.route
        run = renewal.call(self.repository.transition,
            run_id=run.run_id,
            worker_id=self.worker_id,
            fencing_token=run.fencing_token,
            state=RunState.RUNNING,
            kind=EventKind.PROVIDER_ADMITTED,
            payload={"cost_class": route.cost_class},
            provider=route.provider,
            model=route.model,
        )

        controlled = self._apply_control_if_requested(run, renewal)
        if controlled is not None:
            return controlled

        request_key = new_uuid7()
        run = renewal.call(self.repository.mark_provider_request_started,
            run_id=run.run_id,
            worker_id=self.worker_id,
            fencing_token=run.fencing_token,
            request_key=request_key,
            provider=route.provider,
            model=route.model,
        )

        renewal.check()
        try:
            response = self.provider.generate(
                model=route.model,
                prompt=run.prompt,
                request_key=request_key,
            )
        except ProviderFailure as exc:
            waiting_for_free_quota = exc.code == "RATE_LIMITED"
            return renewal.call(self.repository.transition,
                run_id=run.run_id,
                worker_id=self.worker_id,
                fencing_token=run.fencing_token,
                state=(
                    RunState.WAITING_PROVIDER
                    if waiting_for_free_quota
                    else RunState.UNKNOWN_OUTCOME
                    if exc.ambiguous_outcome
                    else RunState.FAILED
                ),
                kind=(
                    EventKind.PROVIDER_WAIT
                    if waiting_for_free_quota
                    else EventKind.PROVIDER_OUTCOME_UNKNOWN
                    if exc.ambiguous_outcome
                    else EventKind.RUN_FAILED
                ),
                payload={
                    "provider_error": exc.code,
                    "retry_after_seconds": exc.retry_after_seconds,
                    "ambiguous_outcome": exc.ambiguous_outcome,
                    "request_key": request_key,
                    "paid_fallback": False,
                },
                last_error_code=exc.code,
                release_lease=True,
            )

        run = renewal.call(self.repository.mark_provider_request_completed,
            run_id=run.run_id,
            worker_id=self.worker_id,
            fencing_token=run.fencing_token,
            request_key=request_key,
            response=response,
        )

        if self.repository.get_run_control_state(run.run_id) == "CANCELLED":
            return renewal.call(self.repository.transition,
                run_id=run.run_id,
                worker_id=self.worker_id,
                fencing_token=run.fencing_token,
                state=RunState.CANCELLED,
                kind=EventKind.RUN_CANCELLED,
                payload={
                    "reason": "cancellation arrived after provider request started",
                    "provider_result_persisted": True,
                },
                provider=response.provider,
                model=response.model,
                release_lease=True,
            )

        return renewal.call(self.repository.transition,
            run_id=run.run_id,
            worker_id=self.worker_id,
            fencing_token=run.fencing_token,
            state=RunState.COMPLETED,
            kind=EventKind.RUN_COMPLETED,
            payload={"provider": response.provider, "model": response.model},
            provider=response.provider,
            model=response.model,
            release_lease=True,
        )


def _free_quota_available_from_env() -> bool:
    raw = os.environ.get("AMODEX_FREE_QUOTA_AVAILABLE", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def main() -> None:
    repo = PostgresRunRepository(os.environ["AMODEX_DATABASE_URL"])
    worker = Stage1Worker(
        repository=repo,
        provider=GeminiProvider(api_key=os.environ["GEMINI_API_KEY"]),
        governor=ProviderGovernor.stage1_default(),
        worker_id=f"worker-{new_uuid7()}",
        lease_policy=RunLeasePolicy.from_environment(),
    )
    result = worker.process_once(free_quota_available=_free_quota_available_from_env())
    if result is not None:
        print(result.model_dump_json())


if __name__ == "__main__":
    main()
