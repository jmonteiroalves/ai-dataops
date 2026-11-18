"""Ports implemented by infrastructure adapters.

Business modules depend on these protocols, never on a concrete engine. The
DuckDB adapter is the default; the BigQuery adapter is added in R3 (ADR-001).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class Warehouse(Protocol):
    """Minimal SQL warehouse contract used by the platform."""

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        """Run a statement without returning rows."""

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """Run a query and return rows as dictionaries."""

    def write_rows(self, table: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Idempotently write rows (upsert on the table's primary key)."""

    def table_exists(self, table: str) -> bool:
        """Whether a fully qualified table exists."""

    def close(self) -> None:
        """Release the connection."""


__all__ = ["Warehouse"]
