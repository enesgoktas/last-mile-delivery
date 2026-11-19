"""Small compatibility shims for Python runtime differences."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

try:
    from datetime import UTC
except ImportError:  # pragma: no cover - Python < 3.11
    UTC = timezone.utc  # noqa: UP017

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - Python < 3.11
    class StrEnum(str, Enum):  # noqa: UP042
        """Fallback for Python versions without enum.StrEnum."""


def utcnow_naive() -> datetime:
    """Return the current UTC timestamp without tzinfo for legacy DB columns."""
    return datetime.now(UTC).replace(tzinfo=None)


def to_naive_utc(value: datetime) -> datetime:
    """Coerce *value* to a naive UTC datetime suitable for legacy DB columns.

    * Naive datetimes are returned unchanged (the caller is asserting they are
      already in UTC, matching the convention used by `utcnow_naive`).
    * Aware datetimes are converted to UTC and stripped of `tzinfo`.

    Mirrors the contract pinned by `tests/unit/test_compat.py`. Used by
    services that ingest user-provided ISO strings (which may be aware) and
    need to store them next to existing naive columns without ambient
    `replace(tzinfo=None)` boilerplate sprinkled around the codebase.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
