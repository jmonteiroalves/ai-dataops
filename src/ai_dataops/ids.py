"""Deterministic identifiers.

Every fact row derives its primary key from natural keys so that replays and
backfills update the same row instead of appending duplicates (ADR-005).
"""

from __future__ import annotations

import uuid

_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://ai-dataops.dev")


def deterministic_id(*parts: object) -> str:
    """Return a stable UUIDv5 for the given natural-key parts."""
    value = "|".join(str(part) for part in parts)
    return str(uuid.uuid5(_NAMESPACE, value))


__all__ = ["deterministic_id"]
