"""Quality rule engine behaviour on controlled fixtures."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from ai_dataops.adapters.duckdb import DuckDBWarehouse
from ai_dataops.config import Settings
from ai_dataops.instrumentation.recorder import ExecutionRecorder
from ai_dataops.quality.catalog import load_rules
from ai_dataops.quality.engine import run_quality

DAY = date(2026, 11, 18)


def _execution(
    warehouse: DuckDBWarehouse,
    settings: Settings,
    *,
    data_date: date | None = None,
) -> dict[str, Any]:
    with ExecutionRecorder(
        warehouse,
        "sales_daily",
        DAY,
        obs_dataset=settings.bq_obs_dataset,
        metadata={"data_date": (data_date or DAY).isoformat()},
    ) as recorder:
        recorder.set_counters(read=0, processed=0, rejected=0)
    return warehouse.query(
        f"SELECT * FROM {settings.bq_obs_dataset}.pipeline_executions WHERE logical_date = ?",
        (DAY,),
    )[0]


def _row(
    index: int,
    execution_id: str,
    *,
    order_id: str | None = None,
    customer_id: str | None = "C00001",
    status: str = "completed",
    payment: str = "pix",
    unit_price: float = 10.0,
    quantity: int = 1,
    discount: float = 0.0,
    order_total: float = 10.0,
    order_date: date | None = None,
) -> dict[str, Any]:
    return {
        "row_id": f"row-{index}",
        "execution_id": execution_id,
        "order_id": order_id or f"O{index:06d}",
        "customer_id": customer_id,
        "order_ts": datetime.combine(order_date or DAY, datetime.min.time()),
        "order_date": order_date or DAY,
        "status": status,
        "payment_method": payment,
        "channel": "web",
        "unit_price": unit_price,
        "quantity": quantity,
        "discount": discount,
        "order_total": order_total,
        "ingested_at": datetime.combine(DAY, datetime.min.time()),
    }


def _insert(warehouse: DuckDBWarehouse, execution_id: str, rows: list[dict[str, Any]]) -> None:
    warehouse.append_rows("demo.sales_clean", rows)


def _outcome(
    warehouse: DuckDBWarehouse,
    settings: Settings,
    execution: dict[str, Any],
    rule_id: str,
) -> Any:
    rule = next(
        item for item in load_rules(settings.rules_dir, settings) if item.rule_id == rule_id
    )
    return run_quality(warehouse, settings, execution, [rule])[0]


def test_catalog_has_the_expected_rules(demo_settings: Settings) -> None:
    rules = load_rules(demo_settings.rules_dir, demo_settings)

    assert len(rules) == 11
    schema = next(item for item in rules if item.rule_id == "orders_schema_contract")
    from ai_dataops.pipeline.synthetic import CONTRACT_COLUMNS

    assert tuple(schema.config["expected"]) == CONTRACT_COLUMNS


def test_completeness_passes_and_fails(warehouse: DuckDBWarehouse, demo_settings: Settings) -> None:
    execution = _execution(warehouse, demo_settings)
    rows = [_row(index, execution["execution_id"]) for index in range(100)]
    _insert(warehouse, execution["execution_id"], rows)
    assert (
        _outcome(warehouse, demo_settings, execution, "orders_completeness_customer_id").status
        == "pass"
    )

    rows[0]["customer_id"] = None
    rows[1]["customer_id"] = None
    warehouse.execute("DELETE FROM demo.sales_clean")
    _insert(warehouse, execution["execution_id"], rows)
    outcome = _outcome(warehouse, demo_settings, execution, "orders_completeness_customer_id")

    assert outcome.status == "fail"
    assert outcome.records_failed == 2


def test_uniqueness_detects_duplicates(warehouse: DuckDBWarehouse, demo_settings: Settings) -> None:
    execution = _execution(warehouse, demo_settings)
    rows = [_row(index, execution["execution_id"]) for index in range(100)]
    rows[1]["order_id"] = rows[0]["order_id"]
    _insert(warehouse, execution["execution_id"], rows)

    outcome = _outcome(warehouse, demo_settings, execution, "orders_uniqueness_order_id")

    assert outcome.status == "fail"
    assert outcome.records_failed == 1


def test_integrity_ignores_nulls_but_flags_orphans(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    execution = _execution(warehouse, demo_settings)
    rows = [_row(index, execution["execution_id"]) for index in range(100)]
    rows[0]["customer_id"] = None
    _insert(warehouse, execution["execution_id"], rows)
    assert (
        _outcome(warehouse, demo_settings, execution, "orders_integrity_customer").status == "pass"
    )

    rows[1]["customer_id"] = "C99999"
    warehouse.execute("DELETE FROM demo.sales_clean")
    _insert(warehouse, execution["execution_id"], rows)

    assert (
        _outcome(warehouse, demo_settings, execution, "orders_integrity_customer").status == "fail"
    )


def test_validity_range_and_accepted_values(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    execution = _execution(warehouse, demo_settings)
    rows = [_row(index, execution["execution_id"]) for index in range(100)]
    rows[0]["unit_price"] = 0.0
    rows[1]["status"] = "unknown"
    _insert(warehouse, execution["execution_id"], rows)

    assert (
        _outcome(warehouse, demo_settings, execution, "orders_validity_unit_price").status == "fail"
    )
    assert _outcome(warehouse, demo_settings, execution, "orders_validity_status").status == "fail"


def test_consistency_detects_total_mismatch(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    execution = _execution(warehouse, demo_settings)
    rows = [_row(index, execution["execution_id"]) for index in range(100)]
    rows[0]["order_total"] = 999.0
    _insert(warehouse, execution["execution_id"], rows)

    outcome = _outcome(warehouse, demo_settings, execution, "orders_consistency_total")

    assert outcome.status == "fail"
    assert outcome.records_failed == 1


def test_freshness_uses_the_execution_logical_date(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    data_date = DAY - timedelta(days=2)
    execution = _execution(warehouse, demo_settings, data_date=data_date)
    rows = [_row(index, execution["execution_id"], order_date=data_date) for index in range(20)]
    _insert(warehouse, execution["execution_id"], rows)

    outcome = _outcome(warehouse, demo_settings, execution, "orders_freshness")

    assert outcome.status == "fail"
    assert outcome.observed_value == 2880.0


def test_schema_contract_detects_renamed_column(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    with ExecutionRecorder(
        warehouse,
        "sales_daily",
        DAY,
        obs_dataset=demo_settings.bq_obs_dataset,
        metadata={"data_date": DAY.isoformat()},
    ) as recorder:
        recorder.record_schema(
            "demo.sales_raw",
            [
                ("order_id", "STRING", True),
                ("customer_id", "STRING", True),
                ("order_ts", "STRING", True),
                ("order_date", "STRING", True),
                ("status", "STRING", True),
                ("payment_method", "STRING", True),
                ("channel", "STRING", True),
                ("unit_price", "STRING", True),
                ("quantity", "STRING", True),
                ("discount_pct", "STRING", True),
                ("order_total", "STRING", True),
            ],
        )
        recorder.set_counters(read=0, processed=0, rejected=0)
    execution = warehouse.query(
        f"SELECT * FROM {demo_settings.bq_obs_dataset}.pipeline_executions WHERE logical_date = ?",
        (DAY,),
    )[0]

    outcome = _outcome(warehouse, demo_settings, execution, "orders_schema_contract")

    assert outcome.status == "fail"
    assert "discount" in outcome.details["missing"]
    assert "discount_pct" in outcome.details["unexpected"]


def test_rules_report_insufficient_data_for_empty_partition(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    execution = _execution(warehouse, demo_settings)

    outcome = _outcome(warehouse, demo_settings, execution, "orders_completeness_order_id")

    assert outcome.status == "insufficient_data"
