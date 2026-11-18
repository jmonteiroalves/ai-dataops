"""Detectors, severity, correlation and the labelled backtest."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest

from ai_dataops.adapters.duckdb import DuckDBWarehouse
from ai_dataops.anomaly.backtest import run_backtest
from ai_dataops.anomaly.correlation import correlate
from ai_dataops.anomaly.detectors import detect_all
from ai_dataops.anomaly.severity import (
    apply_modifiers,
    from_pp,
    from_psi,
    from_ratio,
    from_z,
    max_level,
)
from ai_dataops.anomaly.stats import median, psi, robust_z, two_proportion_z
from ai_dataops.config import Settings
from ai_dataops.domain.enums import Severity, SignalType
from ai_dataops.ids import deterministic_id
from ai_dataops.instrumentation.recorder import ExecutionRecorder
from ai_dataops.quality.catalog import load_rules, sync_rules
from ai_dataops.quality.engine import run_quality

DAY = date(2026, 11, 18)


# ------------------------------------------------------------------- statistics
def test_robust_statistics_match_known_values() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]

    assert median(values) == 3.0
    assert robust_z(10.0, values) == pytest.approx(4.7215, rel=1e-3)
    assert robust_z(3.0, values) == 0.0


def test_rate_shift_needs_significance_and_effect() -> None:
    z, pooled, standard_error = two_proportion_z(620, 10_000, 40, 10_000)

    assert z > 3.0
    assert 0.0 < pooled < 0.1
    assert standard_error > 0


def test_psi_is_zero_for_identical_distributions() -> None:
    baseline = [100.0, 200.0, 300.0, 400.0]

    assert psi(baseline, baseline) == pytest.approx(0.0)
    assert psi([400.0, 300.0, 200.0, 100.0], baseline) > 0.4


def test_severity_bands_and_modifiers() -> None:
    assert from_z(9.0) is Severity.CRITICAL
    assert from_z(4.0) is Severity.LOW
    assert from_ratio(0.55) is Severity.HIGH
    assert from_pp(0.058) is Severity.HIGH
    assert from_psi(0.20) is Severity.MEDIUM
    assert max_level(Severity.LOW, Severity.HIGH) is Severity.HIGH
    assert apply_modifiers(Severity.MEDIUM, criticality="tier_1") is Severity.HIGH
    assert apply_modifiers(Severity.MEDIUM, criticality="tier_3") is Severity.LOW


# --------------------------------------------------------------------- helpers
def _execution(
    warehouse: DuckDBWarehouse,
    settings: Settings,
    day: date,
    *,
    scenario: str = "normal",
    volume: int | None = None,
    duration: float | None = None,
    status: str = "succeeded",
) -> dict[str, Any]:
    recorder = ExecutionRecorder(
        warehouse,
        "sales_daily",
        day,
        obs_dataset=settings.bq_obs_dataset,
        scenario_id=scenario,
        metadata={"data_date": day.isoformat()},
    )
    with recorder:
        recorder.set_counters(read=volume or 0, processed=volume or 0, rejected=0)
        if volume is not None:
            recorder.record_metric("pipeline", "sales_daily", "records_processed", volume, "count")
        if status == "failed":
            recorder.finish("failed", error_code="EXTRACT_TIMEOUT", error_message="boom")
    if duration is not None:
        recorder.record_metric("pipeline", "sales_daily", "duration_seconds", duration, "seconds")
    return warehouse.query(
        f"SELECT * FROM {settings.bq_obs_dataset}.pipeline_executions WHERE logical_date = ?",
        (day,),
    )[0]


def _anomaly_row(
    settings: Settings,
    execution: dict[str, Any],
    day: date,
    metric: str,
    severity: str = "high",
) -> dict[str, Any]:
    return {
        "anomaly_id": deterministic_id("test-anomaly", execution["execution_id"], metric),
        "entity_type": "pipeline",
        "entity_id": "sales_daily",
        "metric_name": metric,
        "execution_id": execution["execution_id"],
        "detected_at": datetime.combine(day, datetime.min.time()),
        "window_start": datetime.combine(day, datetime.min.time()),
        "window_end": datetime.combine(day, datetime.min.time()),
        "observed_value": 500.0,
        "expected_value": 1000.0,
        "lower_bound": 900.0,
        "upper_bound": 1100.0,
        "deviation_abs": -500.0,
        "deviation_pct": -0.5,
        "score": -5.0,
        "method": "test",
        "method_version": "1.0",
        "sample_size": 8,
        "severity": severity,
        "confidence": 0.9,
        "signal_type": SignalType.STATISTICAL_ANOMALY.value,
        "status": "open",
        "explanation": {},
        "created_at": datetime.combine(day, datetime.min.time()),
    }


# -------------------------------------------------------------------- detectors
def test_volume_drop_is_detected_and_normal_day_is_not(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    start = DAY - timedelta(days=9)
    execution = None
    for offset in range(9):
        execution = _execution(
            warehouse, demo_settings, start + timedelta(days=offset), volume=1000
        )

    execution = _execution(warehouse, demo_settings, DAY, volume=500, scenario="volume_drop")
    anomalies = detect_all(warehouse, demo_settings, execution)

    volume = [item for item in anomalies if item.metric_name == "records_processed"]
    assert volume
    assert volume[0].observed_value == 500
    assert volume[0].severity in {Severity.HIGH, Severity.CRITICAL}


def test_volume_normal_day_has_no_anomaly(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    start = DAY - timedelta(days=9)
    execution = None
    for offset in range(10):
        execution = _execution(
            warehouse, demo_settings, start + timedelta(days=offset), volume=1000
        )

    anomalies = detect_all(warehouse, demo_settings, execution or {})

    assert [item for item in anomalies if item.metric_name == "records_processed"] == []


def test_duration_floor_ignores_fast_run_noise(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    start = DAY - timedelta(days=9)
    execution = None
    for offset in range(9):
        execution = _execution(
            warehouse, demo_settings, start + timedelta(days=offset), duration=0.05
        )
    execution = _execution(warehouse, demo_settings, DAY, duration=0.12)
    assert [
        item
        for item in detect_all(warehouse, demo_settings, execution or {})
        if item.metric_name == "duration_seconds"
    ] == []

    slow = _execution(warehouse, demo_settings, DAY - timedelta(days=1), duration=5.0)
    slow_anomalies = detect_all(warehouse, demo_settings, slow)

    assert [item for item in slow_anomalies if item.metric_name == "duration_seconds"]


def test_distribution_drift_is_detected(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    clean_key = f"{demo_settings.demo_dataset}.sales_clean.order_total"
    edges = [0.0, 50.0, 100.0, 200.0, 1000.0]
    start = DAY - timedelta(days=9)
    for offset in range(9):
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
                clean_key,
                "histogram.order_total",
                100.0,
                "count",
                dimensions={"edges": edges, "counts": [10, 20, 30, 40]},
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
            clean_key,
            "histogram.order_total",
            100.0,
            "count",
            dimensions={"edges": edges, "counts": [40, 30, 20, 10]},
        )
    execution = warehouse.query(
        f"SELECT * FROM {demo_settings.bq_obs_dataset}.pipeline_executions WHERE logical_date = ?",
        (DAY,),
    )[0]

    anomalies = detect_all(warehouse, demo_settings, execution)

    assert [item for item in anomalies if item.metric_name == "psi.order_total"]


# ------------------------------------------------------------------ correlation
def test_correlation_groups_related_signals(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    execution = _execution(warehouse, demo_settings, DAY, volume=100, status="failed")
    rules = sync_rules(warehouse, demo_settings, load_rules(demo_settings.rules_dir, demo_settings))
    rule = next(item for item in rules if item.rule_id == "orders_completeness_customer_id")
    run = run_quality(
        warehouse, demo_settings, execution, [rule]
    )  # produces insufficient_data, not a fail
    assert run[0].status == "insufficient_data"

    warehouse.write_rows(
        f"{demo_settings.bq_quality_dataset}.quality_rule_results",
        [
            {
                "result_id": "result-fail",
                "execution_id": execution["execution_id"],
                "rule_id": rule.rule_id,
                "rule_version": rule.version,
                "dataset_key": rule.dataset_key,
                "evaluated_at": datetime.combine(DAY, datetime.min.time()),
                "status": "fail",
                "records_evaluated": 100,
                "records_passed": 90,
                "records_failed": 10,
                "pass_rate": 0.9,
                "observed_value": 0.1,
                "threshold": 0.99,
                "severity": "high",
                "details": {},
                "duration_ms": 5,
                "created_at": datetime.combine(DAY, datetime.min.time()),
            }
        ],
    )
    warehouse.write_rows(
        f"{demo_settings.bq_detect_dataset}.anomalies",
        [_anomaly_row(demo_settings, execution, DAY, "records_processed")],
    )

    incident_id = correlate(warehouse, demo_settings, DAY)

    assert incident_id is not None
    incident = warehouse.query(
        f"SELECT * FROM {demo_settings.bq_detect_dataset}.incidents WHERE incident_id = ?",
        (incident_id,),
    )[0]
    assert incident["evidence_count"] == 3
    assert incident["severity"] == "critical"
    assert incident["hypotheses_json"]
    events = warehouse.query(
        f"SELECT COUNT(*) AS n FROM {demo_settings.bq_detect_dataset}.incident_events "
        "WHERE incident_id = ?",
        (incident_id,),
    )[0]["n"]
    assert events == 3


def test_correlation_returns_none_without_signals(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    _execution(warehouse, demo_settings, DAY, volume=100)

    assert correlate(warehouse, demo_settings, DAY) is None


# --------------------------------------------------------------------- backtest
def test_backtest_scores_labelled_days(warehouse: DuckDBWarehouse, demo_settings: Settings) -> None:
    start = DAY - timedelta(days=2)
    _execution(warehouse, demo_settings, start, volume=1000)
    volume_day = _execution(
        warehouse, demo_settings, start + timedelta(days=1), volume=500, scenario="volume_drop"
    )
    null_day = _execution(warehouse, demo_settings, DAY, volume=900, scenario="null_spike")

    warehouse.write_rows(
        f"{demo_settings.bq_detect_dataset}.anomalies",
        [_anomaly_row(demo_settings, volume_day, start + timedelta(days=1), "records_processed")],
    )

    rules = sync_rules(warehouse, demo_settings, load_rules(demo_settings.rules_dir, demo_settings))
    rule = next(item for item in rules if item.rule_id == "orders_completeness_customer_id")
    warehouse.write_rows(
        f"{demo_settings.bq_quality_dataset}.quality_rule_results",
        [
            {
                "result_id": "result-null",
                "execution_id": null_day["execution_id"],
                "rule_id": rule.rule_id,
                "rule_version": rule.version,
                "dataset_key": rule.dataset_key,
                "evaluated_at": datetime.combine(DAY, datetime.min.time()),
                "status": "fail",
                "records_evaluated": 900,
                "records_passed": 850,
                "records_failed": 50,
                "pass_rate": 0.94,
                "observed_value": 0.06,
                "threshold": 0.99,
                "severity": "high",
                "details": {},
                "duration_ms": 5,
                "created_at": datetime.combine(DAY, datetime.min.time()),
            }
        ],
    )

    report = run_backtest(
        warehouse,
        demo_settings,
        end_date=DAY,
        days=3,
        warmup_days=0,
        output_path=demo_settings.reports_dir / "backtest.json",
    )

    assert report.expected_incidents == 2
    assert report.detected_incidents == 2
    assert report.precision == 1.0
    assert report.recall == 1.0
    assert report.per_family["volume"]["recall"] == 1.0
    assert report.per_family["nulls"]["recall"] == 1.0
    assert (demo_settings.reports_dir / "backtest.json").exists()
