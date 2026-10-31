"""Shared strictness, evolution, and canonical serialization semantics."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Immutable authority snapshot with fail-closed field handling.

    Runtime state changes create a newly validated snapshot; models are never mutated
    in place. Subclasses declare identity fields that cannot change across ``evolve``.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        str_strip_whitespace=True,
    )

    IDENTITY_FIELDS: ClassVar[frozenset[str]] = frozenset()

    def evolve(self, **changes: Any) -> Self:
        """Return a fully revalidated snapshot while preserving immutable identity."""

        for field in self.IDENTITY_FIELDS:
            if field in changes and changes[field] != getattr(self, field):
                raise ValueError(f"identity field {field!r} cannot change")

        payload = self.model_dump(mode="python")
        payload.update(changes)
        return self.__class__.model_validate(payload)


def canonical_json(model: BaseModel) -> str:
    """Serialize a contract deterministically for hashing/audit comparisons."""

    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def require_utc(value: datetime, *, field_name: str) -> datetime:
    """Require an aware timestamp and normalize it to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)
