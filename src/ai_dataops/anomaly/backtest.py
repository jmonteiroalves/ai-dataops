"""Backtest detectors against the labelled demo scenarios.

The evaluation is intentionally day-level: an incident is "detected" when the
day produced at least one anomaly, failed rule or failed execution. Per-family
recall shows which detector was responsible.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ai_dataops.config import Settings
from ai_dataops.domain.ports import Warehouse
from ai_dataops.pipeline.scenarios import SCENARIOS
from ai_dataops.timeutil import utcnow

_ANOMALY_FAMILIES = {
    "records_processed": "volume",
    "row_count": "volume",
    "null_rate.customer_id": "nulls",
    "duplicate_rate": "duplicates",
    "duration_seconds": "duration",
    "psi.order_total": "price",
    "histogram.order_total": "price",
    "freshness_lag_minutes": "freshness",
    "failure_streak": "failure",
    "schema_change": "schema",
}

_RULE_FAMILIES = {
    "completeness": "nulls",
    "uniqueness": "duplicates",
    "freshness": "freshness",
    "schema": "schema",
    "integrity": "quality",
    "consistency": "quality",
    "validity": "quality",
}


@dataclass(slots=True)
class BacktestReport:
    start_date: date
    end_date: date
    warmup_days: int
    evaluated_days: int
    expected_incidents: int
    detected_incidents: int
    false_positive_days: int
    precision: float
    recall: float
    mean_detection_delay_days: float
    per_family: dict[str, dict[str, float | int]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "warmup_days": self.warmup_days,
            "evaluated_days": self.evaluated_days,
            "expected_incidents": self.expected_incidents,
            "detected_incidents": self.detected_incidents,
            "false_positive_days": self.false_positive_days,
            "precision": self.precision,
            "recall": self.recall,
            "mean_detection_delay_days": self.mean_detection_delay_days,
            "per_family": self.per_family,
            "generated_at": utcnow().isoformat() + "Z",
        }


def run_backtest(
    warehouse: Warehouse,
    settings: Settings,
    *,
    end_date: date,
    days: int = 90,
    warmup_days: int = 14,
    output_path: Path | None = None,
) -> BacktestReport:
    start_date = end_date - timedelta(days=days - 1)
    warmup_end = start_date + timedelta(days=warmup_days - 1)

    executions = warehouse.query(
        f"""
        SELECT execution_id, logical_date, scenario_id, status
        FROM {settings.bq_obs_dataset}.pipeline_executions
        WHERE logical_date BETWEEN ? AND ?
        ORDER BY logical_date
        """,
        (start_date, end_date),
    )
    anomalies = warehouse.query(
        f"SELECT execution_id, metric_name FROM {settings.bq_detect_dataset}.anomalies"
    )
    violations = warehouse.query(
        f"""
        SELECT r.execution_id, q.dimension
        FROM {settings.bq_quality_dataset}.quality_rule_results r
        JOIN {settings.bq_meta_dataset}.quality_rules q ON q.rule_id = r.rule_id
        WHERE r.status = 'fail'
        """
    )

    anomaly_families: dict[str, set[str]] = defaultdict(set)
    for row in anomalies:
        family = _ANOMALY_FAMILIES.get(str(row["metric_name"]), "other")
        anomaly_families[str(row["execution_id"])].add(family)

    rule_families: dict[str, set[str]] = defaultdict(set)
    for row in violations:
        family = _RULE_FAMILIES.get(str(row["dimension"]), "quality")
        rule_families[str(row["execution_id"])].add(family)

    expected_days = 0
    detected_days = 0
    true_positives = 0
    false_positives = 0
    delays: list[int] = []
    family_expected: dict[str, int] = defaultdict(int)
    family_detected: dict[str, int] = defaultdict(int)

    evaluated = [row for row in executions if row["logical_date"] > warmup_end]
    for row in evaluated:
        execution_id = str(row["execution_id"])
        scenario_id = str(row["scenario_id"] or "normal")
        expected = SCENARIOS.get(scenario_id, SCENARIOS["normal"]).expected_family
        detected = set(anomaly_families[execution_id]) | set(rule_families[execution_id])
        if row["status"] == "failed":
            detected.add("failure")

        if detected:
            detected_days += 1
        if expected:
            expected_days += 1
            family_expected[expected] += 1
            if detected:
                true_positives += 1
                delays.append(0)
                if expected in detected:
                    family_detected[expected] += 1
        elif detected:
            false_positives += 1

    precision = true_positives / detected_days if detected_days else 0.0
    recall = true_positives / expected_days if expected_days else 0.0
    per_family = {
        family: {
            "expected_days": family_expected[family],
            "detected_days": family_detected[family],
            "recall": round(family_detected[family] / family_expected[family], 4)
            if family_expected[family]
            else 0.0,
        }
        for family in sorted(family_expected)
    }

    report = BacktestReport(
        start_date=start_date,
        end_date=end_date,
        warmup_days=warmup_days,
        evaluated_days=len(evaluated),
        expected_incidents=expected_days,
        detected_incidents=true_positives,
        false_positive_days=false_positives,
        precision=round(precision, 4),
        recall=round(recall, 4),
        mean_detection_delay_days=round(sum(delays) / len(delays), 2) if delays else 0.0,
        per_family=per_family,
    )

    target = output_path or (settings.reports_dir / "backtest.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return report


__all__ = ["BacktestReport", "run_backtest"]
