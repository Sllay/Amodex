"""Durable Candidate/Promotion persistence for the Stage 4 Promotion Gate."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from amodex.contracts.models import (
    Candidate,
    CandidateStatus,
    Promotion,
    PromotionMethod,
    PromotionStatus,
)


class PromotionPersistenceError(RuntimeError):
    pass


class PromotionStateConflict(PromotionPersistenceError):
    pass


class PostgresPromotionRepository:
    """PostgreSQL authority for candidates and promotion intent/results.

    The idempotency key is globally unique. Repeating the same logical promotion
    returns the existing record; changing its candidate/target under the same key
    fails closed.
    """

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    def create_candidate(self, candidate: Candidate) -> Candidate:
        payload = candidate.model_dump(mode="json")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO amodex_candidates (
                                candidate_id, task_id, validated_checkpoint_id,
                                source_sha, expected_target_ref, expected_target_sha,
                                changed_paths, dependency_paths, evidence_ids,
                                status, created_at, updated_at
                            )
                            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s)
                            RETURNING *
                            """,
                            (
                                payload["candidate_id"],
                                payload["task_id"],
                                payload["validated_checkpoint_id"],
                                payload["source_sha"],
                                payload["expected_target_ref"],
                                payload["expected_target_sha"],
                                json.dumps(payload["changed_paths"]),
                                json.dumps(payload["dependency_paths"]),
                                json.dumps(payload["evidence_ids"]),
                                payload["status"],
                                candidate.created_at,
                                candidate.created_at,
                            ),
                        )
                        row = cur.fetchone()
                        assert row is not None
                        return _candidate_from_row(row)
            except psycopg.errors.UniqueViolation:
                conn.rollback()

        existing = self.find_candidate_identity(
            validated_checkpoint_id=str(candidate.validated_checkpoint_id),
            expected_target_ref=candidate.expected_target_ref,
            expected_target_sha=candidate.expected_target_sha,
        )
        if existing is None:
            raise PromotionPersistenceError("candidate uniqueness conflict without recoverable row")
        if (
            existing.task_id != candidate.task_id
            or existing.source_sha != candidate.source_sha
            or existing.changed_paths != candidate.changed_paths
            or existing.dependency_paths != candidate.dependency_paths
            or existing.evidence_ids != candidate.evidence_ids
        ):
            raise PromotionPersistenceError("candidate identity reused with different authoritative payload")
        return existing

    def get_candidate(self, candidate_id: str) -> Candidate | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM amodex_candidates WHERE candidate_id=%s",
                    (candidate_id,),
                )
                row = cur.fetchone()
        return _candidate_from_row(row) if row else None

    def find_candidate_identity(
        self,
        *,
        validated_checkpoint_id: str,
        expected_target_ref: str,
        expected_target_sha: str,
    ) -> Candidate | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM amodex_candidates
                    WHERE validated_checkpoint_id=%s
                      AND expected_target_ref=%s
                      AND expected_target_sha=%s
                    """,
                    (validated_checkpoint_id, expected_target_ref, expected_target_sha),
                )
                row = cur.fetchone()
        return _candidate_from_row(row) if row else None

    def set_candidate_status(self, *, candidate_id: str, status: CandidateStatus) -> Candidate:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE amodex_candidates
                        SET status=%s, updated_at=NOW()
                        WHERE candidate_id=%s
                        RETURNING *
                        """,
                        (status.value, candidate_id),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise PromotionPersistenceError(f"unknown candidate {candidate_id}")
                    return _candidate_from_row(row)

    def create_promotion_intent(self, promotion: Promotion) -> Promotion:
        if promotion.status != PromotionStatus.INTENT_PERSISTED:
            raise ValueError("new promotion must begin as INTENT_PERSISTED")
        payload = promotion.model_dump(mode="json")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO amodex_promotions (
                                promotion_id, candidate_id, idempotency_key,
                                target_ref, expected_target_sha, method, status,
                                integration_sha, target_post_sha,
                                integration_evidence_ids,
                                created_at, updated_at, reconciled_at, detail
                            )
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
                            RETURNING *
                            """,
                            (
                                payload["promotion_id"],
                                payload["candidate_id"],
                                payload["idempotency_key"],
                                payload["target_ref"],
                                payload["expected_target_sha"],
                                payload["method"],
                                payload["status"],
                                payload["integration_sha"],
                                payload["target_post_sha"],
                                json.dumps(payload["integration_evidence_ids"]),
                                promotion.created_at,
                                promotion.updated_at,
                                promotion.reconciled_at,
                                promotion.detail,
                            ),
                        )
                        row = cur.fetchone()
                        assert row is not None
                        return _promotion_from_row(row)
            except psycopg.errors.UniqueViolation:
                conn.rollback()

        existing = self.get_promotion_by_idempotency(promotion.idempotency_key)
        if existing is None:
            raise PromotionPersistenceError("promotion idempotency conflict without recoverable row")
        if (
            existing.candidate_id != promotion.candidate_id
            or existing.target_ref != promotion.target_ref
            or existing.expected_target_sha != promotion.expected_target_sha
            or existing.method != promotion.method
        ):
            raise PromotionPersistenceError(
                "promotion idempotency key reused for different candidate or target"
            )
        return existing

    def get_promotion(self, promotion_id: str) -> Promotion | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM amodex_promotions WHERE promotion_id=%s",
                    (promotion_id,),
                )
                row = cur.fetchone()
        return _promotion_from_row(row) if row else None

    def get_promotion_by_idempotency(self, idempotency_key: str) -> Promotion | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM amodex_promotions WHERE idempotency_key=%s",
                    (idempotency_key,),
                )
                row = cur.fetchone()
        return _promotion_from_row(row) if row else None

    def transition_promotion(
        self,
        *,
        promotion_id: str,
        expected_statuses: tuple[PromotionStatus, ...],
        status: PromotionStatus,
        integration_sha: str | None = None,
        target_post_sha: str | None = None,
        integration_evidence_ids: tuple[str, ...] | None = None,
        detail: str | None = None,
        reconciled: bool = False,
    ) -> Promotion:
        if not expected_statuses:
            raise ValueError("expected_statuses cannot be empty")
        now = datetime.now(timezone.utc)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM amodex_promotions WHERE promotion_id=%s FOR UPDATE",
                        (promotion_id,),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise PromotionPersistenceError(f"unknown promotion {promotion_id}")
                    current = PromotionStatus(str(row["status"]))
                    if current not in expected_statuses:
                        raise PromotionStateConflict(
                            f"promotion {promotion_id} state is {current.value}; expected "
                            f"{[item.value for item in expected_statuses]!r}"
                        )
                    evidence = (
                        tuple(integration_evidence_ids)
                        if integration_evidence_ids is not None
                        else tuple(row["integration_evidence_ids"] or ())
                    )
                    new_integration_sha = integration_sha or row["integration_sha"]
                    new_target_post_sha = target_post_sha or row["target_post_sha"]
                    reconciled_at = now if reconciled else row["reconciled_at"]
                    cur.execute(
                        """
                        UPDATE amodex_promotions
                        SET status=%s,
                            integration_sha=%s,
                            target_post_sha=%s,
                            integration_evidence_ids=%s::jsonb,
                            detail=%s,
                            reconciled_at=%s,
                            updated_at=%s
                        WHERE promotion_id=%s
                        RETURNING *
                        """,
                        (
                            status.value,
                            new_integration_sha,
                            new_target_post_sha,
                            json.dumps(evidence),
                            detail if detail is not None else row["detail"],
                            reconciled_at,
                            now,
                            promotion_id,
                        ),
                    )
                    updated = cur.fetchone()
                    assert updated is not None
                    return _promotion_from_row(updated)

    def count_promotions_for_idempotency(self, idempotency_key: str) -> int:
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM amodex_promotions WHERE idempotency_key=%s",
                    (idempotency_key,),
                )
                row = cur.fetchone()
                assert row is not None
                return int(row[0])


def _candidate_from_row(row: dict[str, object]) -> Candidate:
    return Candidate(
        candidate_id=str(row["candidate_id"]),
        task_id=str(row["task_id"]),
        validated_checkpoint_id=str(row["validated_checkpoint_id"]),
        source_sha=str(row["source_sha"]),
        expected_target_ref=str(row["expected_target_ref"]),
        expected_target_sha=str(row["expected_target_sha"]),
        changed_paths=tuple(row["changed_paths"] or ()),
        dependency_paths=tuple(row["dependency_paths"] or ()),
        evidence_ids=tuple(row["evidence_ids"] or ()),
        status=CandidateStatus(str(row["status"])),
        created_at=row["created_at"],
    )


def _promotion_from_row(row: dict[str, object]) -> Promotion:
    return Promotion(
        promotion_id=str(row["promotion_id"]),
        candidate_id=str(row["candidate_id"]),
        idempotency_key=str(row["idempotency_key"]),
        target_ref=str(row["target_ref"]),
        expected_target_sha=str(row["expected_target_sha"]),
        method=PromotionMethod(str(row["method"])),
        status=PromotionStatus(str(row["status"])),
        integration_sha=(str(row["integration_sha"]) if row["integration_sha"] else None),
        target_post_sha=(str(row["target_post_sha"]) if row["target_post_sha"] else None),
        integration_evidence_ids=tuple(row["integration_evidence_ids"] or ()),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        reconciled_at=row["reconciled_at"],
        detail=(str(row["detail"]) if row["detail"] is not None else None),
    )
