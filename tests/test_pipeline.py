"""End-to-end pipeline behaviour on the local engine."""

from __future__ import annotations

from datetime import date

import pytest

from ai_dataops.adapters.duckdb import DuckDBWarehouse
from ai_dataops.config import Settings
from ai_dataops.errors import ExternalServiceError, SchemaError
from ai_dataops.pipeline.sales import run_sales_pipeline
from ai_dataops.pipeline.scenarios import DayPlan
from ai_dataops.pipeline.synthetic import generate_day, read_manifest, read_raw

DAY = date(2026, 11, 17)


def _prepare(settings: Settings, scenario: str, volume: int = 200) -> None:
    generate_day(settings.landing_dir, DayPlan(DAY, scenario, 99), volume=volume)


def _execution(warehouse: DuckDBWarehouse, settings: Settings) -> dict[str, object]:
    return warehouse.query(
        f"SELECT * FROM {settings.bq_obs_dataset}.pipeline_executions WHERE logical_date = ?",
        (DAY,),
    )[0]


def test_normal_run_writes_facts_and_metrics(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    _prepare(demo_settings, "normal")
    run_sales_pipeline(warehouse, demo_settings, DAY)

    execution = _execution(warehouse, demo_settings)
    assert execution["status"] == "succeeded"
    assert execution["records_processed"] > 0
    assert execution["records_read"] == (
        execution["records_processed"] + execution["records_rejected"]
    )

    clean = warehouse.query(
        "SELECT COUNT(*) AS n FROM demo.sales_clean WHERE order_date = ?", (DAY,)
    )[0]["n"]
    assert clean == execution["records_processed"]

    metrics = warehouse.query(
        f"SELECT COUNT(*) AS n FROM {demo_settings.bq_obs_dataset}.metric_observations "
        "WHERE execution_id = ?",
        (execution["execution_id"],),
    )[0]["n"]
    assert metrics >= 8

    daily = warehouse.query(
        "SELECT orders_count, gross_revenue FROM demo.sales_daily_metrics WHERE order_date = ?",
        (DAY,),
    )[0]
    assert daily["orders_count"] == clean
    assert daily["gross_revenue"] > 0


def test_replay_is_idempotent(warehouse: DuckDBWarehouse, demo_settings: Settings) -> None:
    _prepare(demo_settings, "normal", volume=120)
    run_sales_pipeline(warehouse, demo_settings, DAY)
    first = warehouse.query(
        "SELECT (SELECT COUNT(*) FROM demo.sales_raw) AS raw, "
        "(SELECT COUNT(*) FROM demo.sales_clean) AS clean"
    )[0]

    run_sales_pipeline(warehouse, demo_settings, DAY)
    second = warehouse.query(
        "SELECT (SELECT COUNT(*) FROM demo.sales_raw) AS raw, "
        "(SELECT COUNT(*) FROM demo.sales_clean) AS clean"
    )[0]

    assert first == second
    assert (
        warehouse.query(
            f"SELECT COUNT(*) AS n FROM {demo_settings.bq_obs_dataset}.pipeline_executions"
        )[0]["n"]
        == 1
    )


def test_schema_change_fails_before_writing_clean_data(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    _prepare(demo_settings, "schema_change")

    with pytest.raises(SchemaError):
        run_sales_pipeline(warehouse, demo_settings, DAY)

    execution = _execution(warehouse, demo_settings)
    assert execution["status"] == "failed"
    assert execution["error_code"] == "schema_error"
    assert (
        warehouse.query("SELECT COUNT(*) AS n FROM demo.sales_clean WHERE order_date = ?", (DAY,))[
            0
        ]["n"]
        == 0
    )

    snapshot = warehouse.query(
        f"SELECT column_name FROM {demo_settings.bq_obs_dataset}.schema_snapshots "
        "WHERE dataset_key = 'demo.sales_raw'"
    )
    assert "discount_pct" in {row["column_name"] for row in snapshot}


def test_ingestion_failure_records_partial_read(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    _prepare(demo_settings, "ingestion_failure")
    manifest = read_manifest(demo_settings.landing_dir)[DAY.isoformat()]
    _, rows = read_raw(demo_settings.landing_dir / f"orders_{DAY.isoformat()}.csv")

    with pytest.raises(ExternalServiceError):
        run_sales_pipeline(warehouse, demo_settings, DAY)

    execution = _execution(warehouse, demo_settings)
    assert execution["status"] == "failed"
    assert execution["error_code"] == "external_service_error"
    assert 0 < execution["records_read"] < len(rows)
    assert manifest["scenario_id"] == "ingestion_failure"


def test_stale_data_writes_an_older_partition(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    _prepare(demo_settings, "stale_data")
    run_sales_pipeline(warehouse, demo_settings, DAY)

    dates = {
        row["order_date"]
        for row in warehouse.query("SELECT DISTINCT order_date FROM demo.sales_clean")
    }
    assert dates == {date(2026, 11, 15)}

    execution = _execution(warehouse, demo_settings)
    assert execution["status"] == "succeeded"
