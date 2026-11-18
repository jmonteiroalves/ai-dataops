"""BigQuery warehouse adapter (cloud engine).

SQL differences from DuckDB are contained here: positional parameters, MERGE for logical
keys, and load jobs for append-only demo tables (batch loads are free).
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from ai_dataops.adapters.schema import column_types, primary_keys
from ai_dataops.config import Settings
from ai_dataops.errors import ConfigurationError, WarehouseError

_CHUNK_ROWS = 100


def build_merge_statement(
    table: str,
    columns: Sequence[str],
    keys: Sequence[str],
    row_count: int,
) -> str:
    """Build the parameterised MERGE used for logical-key upserts."""
    structs = []
    index = 0
    for _ in range(row_count):
        fields = []
        for column in columns:
            fields.append(f"@p{index} AS {column}")
            index += 1
        structs.append(f"STRUCT({', '.join(fields)})")
    source = "SELECT * FROM UNNEST([" + ", ".join(structs) + "])"
    on_clause = " AND ".join(f"T.{key} = S.{key}" for key in keys)
    updates = ", ".join(f"{column} = S.{column}" for column in columns if column not in keys)
    matched = f"WHEN MATCHED THEN UPDATE SET {updates} " if updates else ""
    insert_columns = ", ".join(columns)
    insert_values = ", ".join(f"S.{column}" for column in columns)
    return (
        f"MERGE `{table}` T USING ({source}) S ON {on_clause} "
        f"{matched}"
        f"WHEN NOT MATCHED THEN INSERT ({insert_columns}) VALUES ({insert_values})"
    )


def _param_value(value: Any) -> Any:
    """Values for query parameters keep native date/datetime types."""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, default=str)
    return value


def _json_value(value: Any, column_type: str) -> Any:
    """Values for JSON load jobs use ISO strings for temporal types."""
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, default=str)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if column_type == "STRING":
        return str(value)
    return value


class BigQueryWarehouse:
    """Thin, idempotent wrapper around BigQuery."""

    def __init__(self, settings: Settings) -> None:
        if not settings.project_id:
            raise ConfigurationError("DATAOPS_PROJECT_ID is required for the BigQuery engine")
        try:
            bigquery = importlib.import_module("google.cloud.bigquery")
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ConfigurationError("google-cloud-bigquery is not installed") from exc

        self._bigquery = bigquery
        self._settings = settings
        self._client = bigquery.Client(project=settings.project_id, location=settings.region)
        self._keys = primary_keys(settings)
        self._types = column_types(settings)

    def __enter__(self) -> BigQueryWarehouse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        config = self._job_config(sql, params)
        try:
            self._client.query(sql, job_config=config, location=self._settings.region).result()
        except Exception as exc:
            raise WarehouseError("query failed", context={"sql": sql[:400]}) from exc

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        config = self._job_config(sql, params)
        try:
            result = self._client.query(
                sql, job_config=config, location=self._settings.region
            ).result()
        except Exception as exc:
            raise WarehouseError("query failed", context={"sql": sql[:400]}) from exc
        return [dict(row.items()) for row in result]

    def write_rows(self, table: str, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        keys = self._keys.get(table)
        if not keys:
            raise ConfigurationError("no logical key defined for table", context={"table": table})
        columns = list(rows[0].keys())
        for start in range(0, len(rows), _CHUNK_ROWS):
            chunk = rows[start : start + _CHUNK_ROWS]
            self._merge_chunk(table, columns, keys, chunk)
        return len(rows)

    def append_rows(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        chunk_size: int = 1000,
    ) -> int:
        if not rows:
            return 0
        bigquery = self._bigquery
        written = 0
        for start in range(0, len(rows), chunk_size):
            chunk = rows[start : start + chunk_size]
            payload = []
            for row in chunk:
                payload.append(
                    {
                        column: _json_value(value, self._types.get((table, column), "STRING"))
                        for column, value in row.items()
                    }
                )
            config = bigquery.LoadJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            )
            try:
                job = self._client.load_table_from_json(
                    payload, table, job_config=config, location=self._settings.region
                )
                job.result()
            except Exception as exc:
                raise WarehouseError(
                    "append failed", context={"table": table, "rows": len(chunk)}
                ) from exc
            written += len(chunk)
        return written

    def table_exists(self, table: str) -> bool:
        from google.api_core.exceptions import NotFound

        try:
            self._client.get_table(table)
        except NotFound:
            return False
        return True

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ helpers
    def _job_config(self, sql: str, params: Sequence[Any]) -> Any:
        if not params:
            return None
        bigquery = self._bigquery
        return bigquery.QueryJobConfig(query_parameters=self._parameters(sql, params))

    def _parameters(self, sql: str, params: Sequence[Any]) -> list[Any]:
        del sql
        parameters = []
        for value in params:
            column_type = self._infer_type(None, value)
            parameters.append(
                self._bigquery.ScalarQueryParameter(None, column_type, _param_value(value))
            )
        return parameters

    @staticmethod
    def _infer_type(column: str | None, value: Any) -> str:
        if isinstance(value, bool):
            return "BOOL"
        if isinstance(value, int):
            return "INT64"
        if isinstance(value, float):
            return "FLOAT64"
        if isinstance(value, datetime):
            return "TIMESTAMP"
        if isinstance(value, date):
            return "DATE"
        return "STRING"

    def _merge_chunk(
        self,
        table: str,
        columns: list[str],
        keys: Sequence[str],
        rows: Sequence[Mapping[str, Any]],
    ) -> None:
        sql = build_merge_statement(table, columns, keys, len(rows))
        parameters = []
        index = 0
        for row in rows:
            for column in columns:
                column_type = self._types.get((table, column), "STRING")
                parameters.append(
                    self._bigquery.ScalarQueryParameter(
                        f"p{index}", column_type, _param_value(row[column])
                    )
                )
                index += 1
        try:
            self._client.query(
                sql,
                job_config=self._bigquery.QueryJobConfig(query_parameters=parameters),
                location=self._settings.region,
            ).result()
        except Exception as exc:
            raise WarehouseError(
                "merge failed", context={"table": table, "rows": len(rows)}
            ) from exc


__all__ = ["BigQueryWarehouse", "build_merge_statement"]
