"""DuckDB warehouse adapter (default local engine, ADR-001)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, Self

import duckdb

from ai_dataops.errors import ConfigurationError, WarehouseError

_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _identifier(name: str) -> str:
    if not _IDENTIFIER.fullmatch(name):
        raise ConfigurationError(f"invalid SQL identifier: {name!r}")
    return name


def _qualified(table: str) -> str:
    parts = table.split(".")
    if len(parts) != 2:
        raise ConfigurationError(f"table must be schema-qualified: {table!r}")
    return ".".join(_identifier(part) for part in parts)


def _serialise(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, default=str)
    if isinstance(value, (date, datetime, bool, int, float, str)) or value is None:
        return value
    return str(value)


class DuckDBWarehouse:
    """Thin, idempotent wrapper around a DuckDB database file."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._connection = duckdb.connect(database=str(path))
        except duckdb.Error as exc:  # pragma: no cover - environment failure
            raise WarehouseError(
                f"cannot open DuckDB at {path}", context={"path": str(path)}
            ) from exc

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        try:
            self._connection.execute(sql, params)
        except duckdb.Error as exc:
            raise WarehouseError("query failed", context={"sql": sql[:400]}) from exc

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        try:
            cursor = self._connection.execute(sql, params)
            description = cursor.description or []
            columns = [column[0] for column in description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        except duckdb.Error as exc:
            raise WarehouseError("query failed", context={"sql": sql[:400]}) from exc

    def write_rows(self, table: str, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        destination = _qualified(table)
        columns = [_identifier(column) for column in rows[0]]
        placeholders = ", ".join("?" for _ in columns)
        column_list = ", ".join(columns)
        sql = f"INSERT OR REPLACE INTO {destination} ({column_list}) VALUES ({placeholders})"
        values = [tuple(_serialise(row[column]) for column in columns) for row in rows]
        try:
            self._connection.executemany(sql, values)
        except duckdb.Error as exc:
            raise WarehouseError(
                "write failed", context={"table": destination, "rows": len(rows)}
            ) from exc
        return len(rows)

    def append_rows(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        chunk_size: int = 500,
    ) -> int:
        """Append rows in batched statements. Use for partition-replaced facts."""
        if not rows:
            return 0
        destination = _qualified(table)
        columns = [_identifier(column) for column in rows[0]]
        column_list = ", ".join(columns)
        row_placeholder = "(" + ", ".join("?" for _ in columns) + ")"
        written = 0
        for start in range(0, len(rows), chunk_size):
            chunk = rows[start : start + chunk_size]
            values_sql = ", ".join(row_placeholder for _ in chunk)
            sql = f"INSERT INTO {destination} ({column_list}) VALUES {values_sql}"
            params = [_serialise(row[column]) for row in chunk for column in columns]
            try:
                self._connection.execute(sql, params)
            except duckdb.Error as exc:
                raise WarehouseError(
                    "append failed", context={"table": destination, "rows": len(chunk)}
                ) from exc
            written += len(chunk)
        return written

    def table_exists(self, table: str) -> bool:
        schema, name = _qualified(table).split(".")
        rows = self.query(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = ? AND table_name = ? LIMIT 1",
            (schema, name),
        )
        return bool(rows)

    def close(self) -> None:
        self._connection.close()


__all__ = ["DuckDBWarehouse"]
