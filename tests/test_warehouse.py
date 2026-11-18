"""Warehouse adapter contract."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from ai_dataops.adapters.duckdb import DuckDBWarehouse
from ai_dataops.errors import ConfigurationError, WarehouseError


def test_schema_is_created_idempotently(warehouse: DuckDBWarehouse) -> None:
    assert warehouse.table_exists("dataops_obs.pipeline_executions")
    assert warehouse.table_exists("demo.sales_clean")


def test_write_rows_is_idempotent(warehouse: DuckDBWarehouse) -> None:
    before = warehouse.query("SELECT COUNT(*) AS n FROM dataops_meta.scenarios")[0]["n"]
    rows = [
        {
            "scenario_id": "normal",
            "name": "Normal",
            "description": "d",
            "config_json": {"a": 1},
            "expected_signal": None,
            "is_active": True,
        }
    ]
    warehouse.write_rows("dataops_meta.scenarios", rows)
    warehouse.write_rows("dataops_meta.scenarios", rows)

    after = warehouse.query("SELECT COUNT(*) AS n FROM dataops_meta.scenarios")[0]["n"]
    assert before == after


def test_append_rows_adds_rows(warehouse: DuckDBWarehouse) -> None:
    row = {
        "row_id": "r1",
        "execution_id": "e1",
        "order_id": "o1",
        "customer_id": None,
        "order_ts": datetime(2026, 11, 18, 12),
        "order_date": date(2026, 11, 18),
        "status": "completed",
        "payment_method": "pix",
        "channel": "web",
        "unit_price": 10.0,
        "quantity": 1,
        "discount": 0.0,
        "order_total": 10.0,
        "source_file": "f",
        "ingested_at": datetime(2026, 11, 18, 12),
    }
    assert warehouse.append_rows("demo.sales_raw", [row, row]) == 2
    assert warehouse.query("SELECT COUNT(*) AS n FROM demo.sales_raw")[0]["n"] == 2


def test_invalid_identifier_is_rejected(warehouse: DuckDBWarehouse) -> None:
    with pytest.raises(ConfigurationError):
        warehouse.write_rows("demo.sales_raw; DROP TABLE x", [{"a": 1}])


def test_query_errors_are_translated(warehouse: DuckDBWarehouse) -> None:
    with pytest.raises(WarehouseError):
        warehouse.query("SELECT * FROM demo.does_not_exist")
