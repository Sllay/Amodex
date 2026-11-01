"""Product-facing Promotion Gate execution boundary.

The CLI/web may request a promotion, but the actual mutation still goes through the
Stage 4 PromotionGate with exact-SHA validation. No product client receives Git
mutation authority.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from amodex.git.promotion import CommandValidator, LocalGitPromotionBackend, PromotionGate
from amodex.storage.promotion import PostgresPromotionRepository


class ProductPromotionExecutor(Protocol):
    def promote(self, *, candidate_id: str, idempotency_key: str) -> dict[str, object]:
        ...


@dataclass(slots=True)
class ConfiguredPromotionExecutor:
    repository_path: Path
    dsn: str
    validator_command: tuple[str, ...]

    @classmethod
    def from_strings(
        cls,
        *,
        repository_path: str,
        dsn: str,
        validator_command: str,
    ) -> "ConfiguredPromotionExecutor":
        command = tuple(shlex.split(validator_command))
        if not command:
            raise ValueError("promotion validator command must not be empty")
        return cls(Path(repository_path), dsn, command)

    def promote(self, *, candidate_id: str, idempotency_key: str) -> dict[str, object]:
        repository = PostgresPromotionRepository(self.dsn)
        gate = PromotionGate(
            backend=LocalGitPromotionBackend(self.repository_path),
            repository=repository,
        )
        validator = CommandValidator(
            validator_id="stage6-product-promotion",
            validator_version="1",
            command=self.validator_command,
        )

        existing = repository.get_promotion_by_idempotency(idempotency_key)
        if existing is not None:
            if str(existing.candidate_id) != candidate_id:
                raise ValueError(
                    "promotion idempotency identity already belongs to a different candidate"
                )
            outcome = gate.reconcile(
                idempotency_key=idempotency_key,
                validator=validator,
            )
        else:
            outcome = gate.promote(
                candidate_id=candidate_id,
                idempotency_key=idempotency_key,
                validator=validator,
            )
        return {
            "candidate_id": str(outcome.candidate.candidate_id),
            "promotion_id": str(outcome.promotion.promotion_id),
            "status": outcome.promotion.status.value,
            "integration_sha": outcome.promotion.integration_sha,
            "target_post_sha": outcome.promotion.target_post_sha,
            "freshness": outcome.freshness.disposition.value,
        }
