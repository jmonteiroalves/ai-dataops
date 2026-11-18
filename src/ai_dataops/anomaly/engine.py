"""Detect and persist anomalies for one execution."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from ai_dataops.anomaly.detectors import detect_all
from ai_dataops.config import Settings
from ai_dataops.domain.models import AnomalyRecord
from ai_dataops.domain.ports import Warehouse
from ai_dataops.ids import deterministic_id
from ai_dataops.timeutil import utcnow


def run_detection(
    warehouse: Warehouse,
    settings: Settings,
    execution: Mapping[str, Any],
) -> list[AnomalyRecord]:
    anomalies = detect_all(warehouse, settings, execution)
    logical_date = _logical_date(execution)
    existing = {
        str(row["anomaly_id"]): str(row["status"])
        for row in warehouse.query(
            f"SELECT anomaly_id, status FROM {settings.bq_detect_dataset}.anomalies "
            "WHERE execution_id = ?",
            (execution["execution_id"],),
        )
    }

    for anomaly in anomalies:
        anomaly_id = deterministic_id(
            "anomaly",
            anomaly.entity_type,
            anomaly.entity_id,
            anomaly.metric_name,
            logical_date,
            anomaly.method,
        )
        deviation_abs = anomaly.observed_value - anomaly.expected_value
        deviation_pct = deviation_abs / anomaly.expected_value if anomaly.expected_value else None
        warehouse.write_rows(
            f"{settings.bq_detect_dataset}.anomalies",
            [
                {
                    "anomaly_id": anomaly_id,
                    "entity_type": anomaly.entity_type,
                    "entity_id": anomaly.entity_id,
                    "metric_name": anomaly.metric_name,
                    "execution_id": anomaly.execution_id,
                    "detected_at": anomaly.detected_at,
                    "window_start": anomaly.window_start,
                    "window_end": anomaly.window_end,
                    "observed_value": anomaly.observed_value,
                    "expected_value": anomaly.expected_value,
                    "lower_bound": anomaly.lower_bound,
                    "upper_bound": anomaly.upper_bound,
                    "deviation_abs": round(deviation_abs, 6),
                    "deviation_pct": round(deviation_pct, 6) if deviation_pct is not None else None,
                    "score": anomaly.score,
                    "method": anomaly.method,
                    "method_version": "1.0",
                    "sample_size": anomaly.sample_size,
                    "severity": anomaly.severity.value,
                    "confidence": anomaly.confidence,
                    "signal_type": anomaly.signal_type.value,
                    "status": existing.get(anomaly_id, "open"),
                    "explanation": anomaly.explanation,
                    "created_at": utcnow(),
                }
            ],
        )
    return anomalies


def _logical_date(execution: Mapping[str, Any]) -> date:
    value = execution["logical_date"]
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


__all__ = ["run_detection"]
