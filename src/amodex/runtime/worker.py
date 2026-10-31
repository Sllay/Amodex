"""Stage 1 worker vertical slice."""

from __future__ import annotations
import os
from dataclasses import dataclass
from amodex.contracts.ids import new_uuid7
from amodex.providers.base import ProviderClient, ProviderFailure
from amodex.providers.gemini import GeminiProvider
from amodex.providers.governor import AdmissionStatus, ProviderGovernor
from amodex.storage.postgres import PostgresRunRepository
from .models import EventKind, RunRecord, RunState


@dataclass(slots=True)
class Stage1Worker:
    repository: PostgresRunRepository
    provider: ProviderClient
    governor: ProviderGovernor
    worker_id: str

    def process_once(self, *, free_quota_available: bool = True) -> RunRecord | None:
        run = self.repository.claim_next(worker_id=self.worker_id)
        if run is None: return None
        decision = self.governor.admit(free_quota_available=free_quota_available)
        if decision.status == AdmissionStatus.WAITING_PROVIDER:
            return self.repository.transition(run_id=run.run_id, worker_id=self.worker_id, fencing_token=run.fencing_token, state=RunState.WAITING_PROVIDER, kind=EventKind.PROVIDER_WAIT, payload={"reason":decision.reason}, last_error_code="FREE_QUOTA_UNAVAILABLE", release_lease=True)
        if decision.status != AdmissionStatus.ADMITTED or decision.route is None:
            return self.repository.transition(run_id=run.run_id, worker_id=self.worker_id, fencing_token=run.fencing_token, state=RunState.BLOCKED, kind=EventKind.RUN_FAILED, payload={"reason":decision.reason}, last_error_code="NO_ZERO_COST_ROUTE", release_lease=True)
        route = decision.route
        run = self.repository.transition(run_id=run.run_id, worker_id=self.worker_id, fencing_token=run.fencing_token, state=RunState.RUNNING, kind=EventKind.PROVIDER_ADMITTED, payload={"cost_class":route.cost_class}, provider=route.provider, model=route.model)
        request_key = new_uuid7()
        self.repository.transition(run_id=run.run_id, worker_id=self.worker_id, fencing_token=run.fencing_token, state=RunState.RUNNING, kind=EventKind.PROVIDER_REQUEST_STARTED, payload={"request_key":request_key})
        try:
            response = self.provider.generate(model=route.model, prompt=run.prompt, request_key=request_key)
        except ProviderFailure as exc:
            return self.repository.transition(run_id=run.run_id, worker_id=self.worker_id, fencing_token=run.fencing_token, state=RunState.UNKNOWN_OUTCOME if exc.ambiguous_outcome else RunState.FAILED, kind=EventKind.RUN_FAILED, payload={"provider_error":exc.code,"retry_after_seconds":exc.retry_after_seconds,"ambiguous_outcome":exc.ambiguous_outcome}, last_error_code=exc.code, release_lease=True)
        self.repository.transition(run_id=run.run_id, worker_id=self.worker_id, fencing_token=run.fencing_token, state=RunState.RUNNING, kind=EventKind.PROVIDER_REQUEST_COMPLETED, payload={"usage":response.usage.model_dump(mode="json")})
        return self.repository.transition(run_id=run.run_id, worker_id=self.worker_id, fencing_token=run.fencing_token, state=RunState.COMPLETED, kind=EventKind.RUN_COMPLETED, payload={"provider":response.provider,"model":response.model}, provider=response.provider, model=response.model, result_text=response.text, release_lease=True)


def main() -> None:
    repo = PostgresRunRepository(os.environ["AMODEX_DATABASE_URL"])
    worker = Stage1Worker(repository=repo, provider=GeminiProvider(api_key=os.environ["GEMINI_API_KEY"]), governor=ProviderGovernor.stage1_default(), worker_id=f"worker-{new_uuid7()}")
    result = worker.process_once()
    if result is not None: print(result.model_dump_json())
