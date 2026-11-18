"""Serving snapshot export and dashboard data functions."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ai_dataops.adapters.duckdb import DuckDBWarehouse
from ai_dataops.anomaly.correlation import correlate
from ai_dataops.anomaly.engine import run_detection
from ai_dataops.config import Settings
from ai_dataops.dashboard.data import (
    anomaly_rows,
    diagnosis_view,
    execution_rows,
    failing_rule_rows,
    incident_choices,
    incident_detail,
    load_snapshot,
    overview_cards,
    quality_rows,
    quality_trend,
)
from ai_dataops.pipeline.sales import run_sales_pipeline
from ai_dataops.pipeline.scenarios import DayPlan
from ai_dataops.pipeline.synthetic import generate_day
from ai_dataops.quality.catalog import load_rules, sync_rules
from ai_dataops.quality.engine import run_quality
from ai_dataops.serving.export import export_snapshot

DAY = date(2026, 11, 18)


def test_load_snapshot_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_snapshot(tmp_path / "missing.json")


def _snapshot() -> dict[str, Any]:
    return {
        "manifest": {"end_date": "2026-11-18", "generated_at": "now", "engine": "duckdb"},
        "overview": {
            "pipelines_monitored": 1,
            "pipelines_healthy": 0,
            "pipelines_degraded": 1,
            "open_incidents": 1,
            "average_quality_score": 0.875,
            "total_cost_usd": 0.0123,
        },
        "executions": [
            {
                "logical_date": "2026-11-18",
                "status": "failed",
                "scenario_id": "schema_change",
                "records_processed": 0,
                "records_rejected": 1595,
                "duration_seconds": 1.2,
                "error_code": "schema_error",
            }
        ],
        "quality": {
            "scores": [
                {
                    "dataset_key": "demo.sales_clean",
                    "dimension": "schema",
                    "score": 0.75,
                    "rules_passed": 3,
                    "rules_failed": 1,
                    "rules_error": 0,
                }
            ],
            "trend": [{"logical_date": "2026-11-18", "score": 0.75}],
            "failing_rules": [
                {
                    "logical_date": "2026-11-18",
                    "rule_id": "orders_schema_contract",
                    "dimension": "schema",
                    "observed_value": 0.9,
                    "threshold": 1.0,
                    "severity": "high",
                }
            ],
        },
        "anomalies": [
            {
                "detected_at": "2026-11-18",
                "entity_id": "demo.sales_raw",
                "metric_name": "schema_change",
                "observed_value": 11,
                "expected_value": 11,
                "method": "schema_diff",
                "severity": "critical",
                "status": "open",
            }
        ],
        "incidents": [
            {
                "incident": {
                    "incident_id": "inc-1",
                    "bucket_start": "2026-11-18",
                    "title": "sales_daily: schema change",
                    "severity": "high",
                    "status": "open",
                    "evidence_count": 3,
                    "hypotheses_json": [{"cause": "Upstream contract change", "confidence": 0.7}],
                },
                "events": [{"event_type": "schema_change", "event_ref_id": "a-1"}],
                "diagnosis": {
                    "summary": "A schema change is consistent with the observed failure.",
                    "observed_facts": json.dumps(
                        [{"statement": "The header changed.", "evidence_ids": ["EV-01"]}]
                    ),
                    "affected_indicators": "[]",
                    "hypotheses": json.dumps(
                        [
                            {
                                "cause": "Upstream contract change",
                                "evidence_ids": ["EV-01"],
                                "confidence": 0.7,
                                "verification_steps": ["Compare headers"],
                            }
                        ]
                    ),
                    "impact": json.dumps(
                        {
                            "scope": "daily marts",
                            "affected_consumers": ["dashboard"],
                            "statement": "stale",
                        }
                    ),
                    "investigation_steps": json.dumps(["Compare headers"]),
                    "suggested_actions": json.dumps(
                        [
                            {
                                "action": "Fix the contract",
                                "priority": "high",
                                "requires_human_approval": True,
                            }
                        ]
                    ),
                    "limitations": json.dumps(["Only bundle evidence."]),
                    "confidence": 0.7,
                    "insufficient_evidence": False,
                    "model_id": "gemini-3.5-flash-lite",
                    "prompt_version": 1,
                },
                "recommendations": [{"action": "Fix the contract", "status": "proposed"}],
            }
        ],
        "diagnoses": [{"incident_id": "inc-1", "status": "success"}],
        "operations": {"daily": [], "invalid_diagnoses": 0},
    }


def test_overview_cards_format_values() -> None:
    cards = overview_cards(_snapshot())

    assert cards[0] == {"label": "Pipelines monitored", "value": "1"}
    assert cards[4]["value"] == "87.5%"
    assert cards[5]["value"] == "US$ 0.01"


def test_tables_are_projected_for_display() -> None:
    snapshot = _snapshot()

    assert execution_rows(snapshot)[0]["error"] == "schema_error"
    assert quality_rows(snapshot)[0]["score"] == 0.75
    assert quality_trend(snapshot) == {"score": [0.75]}
    assert failing_rule_rows(snapshot)[0]["rule"] == "orders_schema_contract"
    assert anomaly_rows(snapshot)[0]["metric"] == "schema_change"


def test_incident_detail_and_diagnosis_view() -> None:
    snapshot = _snapshot()
    choices = incident_choices(snapshot)
    assert choices[0][1] == "inc-1"

    detail = incident_detail(snapshot, "inc-1")
    assert detail is not None
    view = diagnosis_view(detail)
    assert view is not None
    assert view["facts"][0]["evidence_ids"] == ["EV-01"]
    assert view["hypotheses"][0]["confidence"] == 0.7
    assert view["actions"][0]["requires_human_approval"] is True
    assert view["model"] == "gemini-3.5-flash-lite"
    assert incident_detail(snapshot, "missing") is None
    assert diagnosis_view({"diagnosis": None}) is None


def test_export_snapshot_from_the_local_warehouse(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    generate_day(demo_settings.landing_dir, DayPlan(DAY, "normal", 5), volume=80)
    run_sales_pipeline(warehouse, demo_settings, DAY)
    execution = warehouse.query(
        f"SELECT * FROM {demo_settings.bq_obs_dataset}.pipeline_executions WHERE logical_date = ?",
        (DAY,),
    )[0]
    rules = sync_rules(warehouse, demo_settings, load_rules(demo_settings.rules_dir, demo_settings))
    run_quality(warehouse, demo_settings, execution, rules)
    run_detection(warehouse, demo_settings, execution)
    correlate(warehouse, demo_settings, DAY)

    path = export_snapshot(warehouse, demo_settings, end_date=DAY)

    assert path.exists()
    snapshot = load_snapshot(path)
    assert snapshot["overview"]["pipelines_monitored"] == 1
    assert snapshot["executions"][0]["status"] == "succeeded"
    assert snapshot["quality"]["scores"]
    assert snapshot["manifest"]["end_date"] == DAY.isoformat()


def test_health_summary_reflects_open_incidents() -> None:
    from ai_dataops.dashboard.data import snapshot_health

    health = snapshot_health(_snapshot())
    assert health["state"] == "Attention"
    assert health["tone"] == "warning"
    assert "incidents are open" in health["sentence"]

    stable = dict(_snapshot())
    stable["incidents"] = []
    stable["overview"] = {**_snapshot()["overview"], "open_incidents": 0}
    assert snapshot_health(stable)["state"] == "Stable"


def test_quality_matrix_and_run_strip() -> None:
    from ai_dataops.dashboard.data import quality_matrix, run_strip

    matrix = quality_matrix(_snapshot())
    assert matrix["datasets"] == ["demo.sales_clean"]
    assert matrix["values"]["demo.sales_clean"]["schema"] == 0.75

    strip = run_strip(_snapshot())
    assert strip[0]["status"] == "failed"
    assert strip[0]["tone"] == "failed"
    assert 0 < strip[0]["height"] <= 100


def test_anomaly_rails_positions_and_formatting() -> None:
    from ai_dataops.dashboard.data import anomaly_rails, format_decimal, format_int, format_ratio

    rails = anomaly_rails(_snapshot())
    assert rails[0]["metric"] == "schema_change"
    assert rails[0]["tone"] == "critical"
    assert rails[0]["observed_pct"] is not None
    assert format_int(1234) == "1,234"
    assert format_ratio(0.875) == "87.5%"
    assert format_decimal(1234.5) == "1,234.50"


def test_severity_counts_only_open_incidents() -> None:
    from ai_dataops.dashboard.data import severity_counts

    assert severity_counts(_snapshot())["high"] == 1


def test_incident_load_weights_severity_by_date() -> None:
    from ai_dataops.dashboard.data import incident_load

    loads, dates = incident_load(_snapshot())
    assert dates == ["2026-11-18"]
    assert loads == [2.0]
