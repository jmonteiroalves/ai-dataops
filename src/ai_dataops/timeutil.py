"""Time helpers.

Timestamps are stored as naive UTC; business dates are resolved in the
configured timezone (``America/Sao_Paulo`` by default).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    """Current UTC time without tzinfo, matching the warehouse TIMESTAMP type."""
    return datetime.now(UTC).replace(tzinfo=None)


def business_today(tz: ZoneInfo) -> date:
    """Today's date in the business timezone."""
    return datetime.now(UTC).astimezone(tz).date()


def parse_date(value: str) -> date:
    """Parse an ISO date (``YYYY-MM-DD``)."""
    return date.fromisoformat(value)


def date_range(end: date, days: int) -> list[date]:
    """Inclusive range of ``days`` dates ending at ``end``."""
    if days < 1:
        raise ValueError("days must be >= 1")
    return [end - timedelta(days=offset) for offset in range(days - 1, -1, -1)]


def at_noon(day: date) -> datetime:
    """Stable observation timestamp for a logical date."""
    return datetime.combine(day, datetime.min.time()).replace(hour=12)


__all__ = ["at_noon", "business_today", "date_range", "parse_date", "utcnow"]
