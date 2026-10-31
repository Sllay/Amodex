"""UUIDv7-compatible identifiers used by Amodex runtime contracts."""

from __future__ import annotations

import secrets
import time
import uuid
from typing import Annotated

from pydantic import AfterValidator


def new_uuid7(*, unix_ms: int | None = None) -> str:
    """Return an RFC 9562 UUIDv7 string.

    ``unix_ms`` exists for deterministic tests. Production callers should omit it.
    The function deliberately owns no global monotonic state; UUIDv7 time ordering is
    preserved at millisecond granularity while randomness supplies uniqueness.
    """

    timestamp_ms = int(time.time_ns() // 1_000_000) if unix_ms is None else unix_ms
    if not 0 <= timestamp_ms < (1 << 48):
        raise ValueError("unix_ms must fit in the 48-bit UUIDv7 timestamp field")

    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (
        (timestamp_ms << 80)
        | (0x7 << 76)
        | (rand_a << 64)
        | (0b10 << 62)
        | rand_b
    )
    return str(uuid.UUID(int=value))


def validate_uuid7(value: str) -> str:
    """Validate and canonicalize a UUIDv7 string."""

    if not isinstance(value, str):
        raise TypeError("UUIDv7 identifiers must be strings")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("invalid UUID string") from exc

    if parsed.version != 7:
        raise ValueError("identifier must be UUIDv7")
    if parsed.variant != uuid.RFC_4122:
        raise ValueError("identifier must use the RFC 4122/RFC 9562 variant")
    return str(parsed)


UUIDv7Str = Annotated[str, AfterValidator(validate_uuid7)]
