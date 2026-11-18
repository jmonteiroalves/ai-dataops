"""Detector branches that the headline scenarios do not cover."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ai_dataops.adapters.duckdb import DuckDBWarehouse
from ai_dataops.anomaly.detectors import detect_all
from ai_dataops.config import Settings
from ai_dataops.instrumentation.recorder import ExecutionRecorder
from ai_dataops.pipeline.synthetic import CONTRACT_COLUMNS

DAY = date(2026, 11, 18)


def _execution(
    warehouse: DuckDBWarehouse,
    settings: Settings,
    day: date,
    *,
    volume: int | None = None,
    status: str = "succeeded",
) -> dict[str, Any]:
    recorder = ExecutionRecorder(
        warehouse,
        "sales_daily",
        day,
        obs_dataset=settings.bq_obs_dataset,
        metadata={"data_date": day.isoformat()},
    )
    with recorder:
        recorder.set_counters(read=volume or 0, processed=volume or 0, rejected=0)
        if volume is not None:
            recorder.record_metric("pipeline", "sales_daily", "records_processed", volume, "count")
        if status == "failed":
            recorder.finish("failed", error_code="EXTRACT_TIMEOUT", error_message="boom")
    return warehouse.query(
        f"SELECT * FROM {settings.bq_obs_dataset}.pipeline_executions WHERE logical_date = ?",
        (day,),
    )[0]


def test_volume_without_history_is_insufficient(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    execution = _execution(warehouse, demo_settings, DAY, volume=1000)

    assert [
        item
        for item in detect_all(warehouse, demo_settings, execution)
        if item.metric_name == "records_processed"
    ] == []


def test_duration_without_history_is_insufficient(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    execution = _execution(warehouse, demo_settings, DAY)
    recorder = ExecutionRecorder(
        warehouse,
        "sales_daily",
        DAY,
        obs_dataset=demo_settings.bq_obs_dataset,
        metadata={"data_date": DAY.isoformat()},
    )
    recorder.record_metric("pipeline", "sales_daily", "duration_seconds", 9.0, "seconds")

    assert [
        item
        for item in detect_all(warehouse, demo_settings, execution)
        if item.metric_name == "duration_seconds"
    ] == []


def test_rate_shift_detects_a_large_effect(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    entity = f"{demo_settings.demo_dataset}.sales_clean.customer_id"
    start = DAY - timedelta(days=7)
    for offset in range(7):
        day = start + timedelta(days=offset)
        recorder = ExecutionRecorder(
            warehouse,
            "sales_daily",
            day,
            obs_dataset=demo_settings.bq_obs_dataset,
            metadata={"data_date": day.isoformat()},
        )
        with recorder:
            recorder.set_counters(read=10_000, processed=10_000, rejected=0)
            recorder.record_metric(
                "column",
                entity,
                "null_rate.customer_id",
                0.004,
                "ratio",
                dimensions={"sample_size": 10_000, "failures": 40},
            )

    recorder = ExecutionRecorder(
        warehouse,
        "sales_daily",
        DAY,
        obs_dataset=demo_settings.bq_obs_dataset,
        metadata={"data_date": DAY.isoformat()},
    )
    with recorder:
        recorder.set_counters(read=10_000, processed=10_000, rejected=0)
        recorder.record_metric(
            "column",
            entity,
            "null_rate.customer_id",
            0.062,
            "ratio",
            dimensions={"sample_size": 10_000, "failures": 620},
        )
    execution = warehouse.query(
        f"SELECT * FROM {demo_settings.bq_obs_dataset}.pipeline_executions WHERE logical_date = ?",
        (DAY,),
    )[0]

    anomalies = [
        item
        for item in detect_all(warehouse, demo_settings, execution)
        if item.metric_name == "null_rate.customer_id"
    ]

    assert anomalies
    assert anomalies[0].severity.value in {"high", "critical"}


def test_rate_shift_ignores_tiny_denominators(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    entity = f"{demo_settings.demo_dataset}.sales_clean.customer_id"
    start = DAY - timedelta(days=3)
    for offset in range(3):
        day = start + timedelta(days=offset)
        recorder = ExecutionRecorder(
            warehouse,
            "sales_daily",
            day,
            obs_dataset=demo_settings.bq_obs_dataset,
            metadata={"data_date": day.isoformat()},
        )
        with recorder:
            recorder.set_counters(read=100, processed=100, rejected=0)
            recorder.record_metric(
                "column",
                entity,
                "null_rate.customer_id",
                0.0,
                "ratio",
                dimensions={"sample_size": 100, "failures": 0},
            )
    recorder = ExecutionRecorder(
        warehouse,
        "sales_daily",
        DAY,
        obs_dataset=demo_settings.bq_obs_dataset,
        metadata={"data_date": DAY.isoformat()},
    )
    with recorder:
        recorder.set_counters(read=100, processed=100, rejected=0)
        recorder.record_metric(
            "column",
            entity,
            "null_rate.customer_id",
            0.2,
            "ratio",
            dimensions={"sample_size": 100, "failures": 20},
        )
    execution = warehouse.query(
        f"SELECT * FROM {demo_settings.bq_obs_dataset}.pipeline_executions WHERE logical_date = ?",
        (DAY,),
    )[0]

    assert [
        item
        for item in detect_all(warehouse, demo_settings, execution)
        if item.metric_name == "null_rate.customer_id"
    ] == []


def test_failure_streak_is_detected(warehouse: DuckDBWarehouse, demo_settings: Settings) -> None:
    _execution(warehouse, demo_settings, DAY - timedelta(days=1), status="failed")
    execution = _execution(warehouse, demo_settings, DAY, status="failed")

    anomalies = [
        item
        for item in detect_all(warehouse, demo_settings, execution)
        if item.metric_name == "failure_streak"
    ]

    assert anomalies
    assert anomalies[0].observed_value == 2
    assert anomalies[0].signal_type.value == "operational_failure"


def test_schema_detector_flags_a_renamed_column(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    columns = [(column, "STRING", True) for column in CONTRACT_COLUMNS if column != "discount"] + [
        ("discount_pct", "STRING", True)
    ]
    with ExecutionRecorder(
        warehouse,
        "sales_daily",
        DAY,
        obs_dataset=demo_settings.bq_obs_dataset,
        metadata={"data_date": DAY.isoformat()},
    ) as recorder:
        recorder.record_schema("demo.sales_raw", columns)
        recorder.set_counters(read=10, processed=0, rejected=10)
    execution = warehouse.query(
        f"SELECT * FROM {demo_settings.bq_obs_dataset}.pipeline_executions WHERE logical_date = ?",
        (DAY,),
    )[0]

    anomalies = [
        item
        for item in detect_all(warehouse, demo_settings, execution)
        if item.metric_name == "schema_change"
    ]

    assert anomalies
    assert anomalies[0].explanation["missing"] == ["discount"]
    assert anomalies[0].signal_type.value == "schema_change"


def test_statistical_freshness_detects_a_lag_below_sla(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    entity = f"{demo_settings.demo_dataset}.sales_clean"
    for offset, lag in enumerate([10.0, 12.0, 15.0, 20.0]):
        day = DAY - timedelta(days=4 - offset)
        recorder = ExecutionRecorder(
            warehouse,
            "sales_daily",
            day,
            obs_dataset=demo_settings.bq_obs_dataset,
            metadata={"data_date": day.isoformat()},
        )
        with recorder:
            recorder.set_counters(read=100, processed=100, rejected=0)
            recorder.record_metric("dataset", entity, "freshness_lag_minutes", lag, "minutes")
    recorder = ExecutionRecorder(
        warehouse,
        "sales_daily",
        DAY,
        obs_dataset=demo_settings.bq_obs_dataset,
        metadata={"data_date": DAY.isoformat()},
    )
    with recorder:
        recorder.set_counters(read=100, processed=100, rejected=0)
        recorder.record_metric("dataset", entity, "freshness_lag_minutes", 120.0, "minutes")
    execution = warehouse.query(
        f"SELECT * FROM {demo_settings.bq_obs_dataset}.pipeline_executions WHERE logical_date = ?",
        (DAY,),
    )[0]

    anomalies = [
        item
        for item in detect_all(warehouse, demo_settings, execution)
        if item.metric_name == "freshness_lag_minutes"
    ]

    assert anomalies


def test_psi_needs_enough_history(warehouse: DuckDBWarehouse, demo_settings: Settings) -> None:
    entity = f"{demo_settings.demo_dataset}.sales_clean.order_total"
    edges = [0.0, 50.0, 100.0, 1000.0]
    for offset in range(3):
        day = DAY - timedelta(days=3 - offset)
        recorder = ExecutionRecorder(
            warehouse,
            "sales_daily",
            day,
            obs_dataset=demo_settings.bq_obs_dataset,
            metadata={"data_date": day.isoformat()},
        )
        with recorder:
            recorder.set_counters(read=100, processed=100, rejected=0)
            recorder.record_metric(
                "column",
                entity,
                "histogram.order_total",
                100.0,
                "count",
                dimensions={"edges": edges, "counts": [10, 20, 30]},
            )
    recorder = ExecutionRecorder(
        warehouse,
        "sales_daily",
        DAY,
        obs_dataset=demo_settings.bq_obs_dataset,
        metadata={"data_date": DAY.isoformat()},
    )
    with recorder:
        recorder.set_counters(read=100, processed=100, rejected=0)
        recorder.record_metric(
            "column",
            entity,
            "histogram.order_total",
            100.0,
            "count",
            dimensions={"edges": edges, "counts": [90, 5, 5]},
        )
    execution = warehouse.query(
        f"SELECT * FROM {demo_settings.bq_obs_dataset}.pipeline_executions WHERE logical_date = ?",
        (DAY,),
    )[0]

    assert [
        item
        for item in detect_all(warehouse, demo_settings, execution)
        if item.metric_name == "psi.order_total"
    ] == []
