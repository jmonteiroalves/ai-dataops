"""Detectors: robust statistics over the observation history."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from ai_dataops.anomaly.severity import (
    apply_modifiers,
    from_pp,
    from_psi,
    from_ratio,
    from_streak,
    from_z,
    max_level,
)
from ai_dataops.anomaly.stats import median, psi, robust_z, scale_from_mad, two_proportion_z
from ai_dataops.config import Settings
from ai_dataops.domain.enums import Severity, SignalType
from ai_dataops.domain.models import AnomalyRecord
from ai_dataops.domain.ports import Warehouse
from ai_dataops.pipeline.synthetic import CONTRACT_COLUMNS
from ai_dataops.timeutil import at_noon

MIN_SAMPLE = 500
_RATE_FLOORS = {
    "null_rate.customer_id": 0.01,
    "duplicate_rate": 0.01,
    "rejection_rate": 0.02,
}


def detect_all(
    warehouse: Warehouse,
    settings: Settings,
    execution: Mapping[str, Any],
) -> list[AnomalyRecord]:
    pipeline_id = str(execution["pipeline_id"])
    criticality = _criticality(warehouse, settings, pipeline_id)
    clean_key = f"{settings.demo_dataset}.sales_clean"
    raw_key = f"{settings.demo_dataset}.sales_raw"
    rate_entities = {
        "null_rate.customer_id": f"{clean_key}.customer_id",
        "duplicate_rate": clean_key,
        "rejection_rate": pipeline_id,
    }

    anomalies: list[AnomalyRecord] = []
    anomalies.extend(_detect_volume(warehouse, settings, execution, criticality))
    anomalies.extend(_detect_duration(warehouse, settings, execution, criticality))
    for metric_name, entity_id in rate_entities.items():
        anomalies.extend(
            _detect_rate_shift(warehouse, settings, execution, metric_name, entity_id, criticality)
        )
    anomalies.extend(_detect_psi(warehouse, settings, execution, criticality, clean_key))
    anomalies.extend(_detect_freshness(warehouse, settings, execution, criticality, clean_key))
    anomalies.extend(_detect_failure_streak(warehouse, settings, execution, criticality))
    anomalies.extend(_detect_schema(warehouse, settings, execution, criticality, raw_key))
    return anomalies


def _criticality(warehouse: Warehouse, settings: Settings, pipeline_id: str) -> str:
    rows = warehouse.query(
        f"SELECT criticality FROM {settings.bq_meta_dataset}.pipelines WHERE pipeline_id = ?",
        (pipeline_id,),
    )
    return str(rows[0]["criticality"]) if rows else "tier_2"


def _current(
    warehouse: Warehouse,
    settings: Settings,
    execution_id: str,
    entity_id: str,
    metric_name: str,
) -> dict[str, Any] | None:
    rows = warehouse.query(
        f"""
        SELECT value, observed_at, dimensions
        FROM {settings.bq_obs_dataset}.metric_observations
        WHERE entity_id = ? AND metric_name = ? AND execution_id = ?
        ORDER BY observed_at DESC
        LIMIT 1
        """,
        (entity_id, metric_name, execution_id),
    )
    if not rows:
        return None
    row = dict(rows[0])
    row["dimensions"] = _dimensions(row.get("dimensions"))
    return row


def _history(
    warehouse: Warehouse,
    settings: Settings,
    execution_id: str,
    entity_id: str,
    metric_name: str,
    limit: int,
    *,
    only_succeeded: bool = False,
) -> list[dict[str, Any]]:
    success_join = (
        f"JOIN {settings.bq_obs_dataset}.pipeline_executions e "
        "ON e.execution_id = o.execution_id AND e.status = 'succeeded'"
        if only_succeeded
        else ""
    )
    rows = warehouse.query(
        f"""
        SELECT o.value, o.observed_at, o.dimensions
        FROM {settings.bq_obs_dataset}.metric_observations o
        {success_join}
        WHERE o.entity_id = ? AND o.metric_name = ? AND o.execution_id <> ?
        ORDER BY o.observed_at DESC
        LIMIT ?
        """,
        (entity_id, metric_name, execution_id, limit),
    )
    history: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["dimensions"] = _dimensions(item.get("dimensions"))
        history.append(item)
    return history


def _dimensions(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        loaded: dict[str, Any] = json.loads(raw)
        return loaded
    if isinstance(raw, dict):
        return raw
    return {}


def _persistent(
    warehouse: Warehouse,
    settings: Settings,
    entity_id: str,
    metric_name: str,
    logical_date: date,
) -> bool:
    rows = warehouse.query(
        f"""
        SELECT 1 FROM {settings.bq_detect_dataset}.anomalies
        WHERE entity_id = ? AND metric_name = ?
          AND CAST(detected_at AS DATE) = ?
          AND severity IN ('medium', 'high', 'critical')
        LIMIT 1
        """,
        (entity_id, metric_name, logical_date - timedelta(days=1)),
    )
    return bool(rows)


def _window(history: list[dict[str, Any]], current: dict[str, Any]) -> tuple[datetime, datetime]:
    observed = [item["observed_at"] for item in history]
    start = min(observed) if observed else current["observed_at"]
    return (start, current["observed_at"])


def _detect_volume(
    warehouse: Warehouse,
    settings: Settings,
    execution: Mapping[str, Any],
    criticality: str,
) -> list[AnomalyRecord]:
    execution_id = str(execution["execution_id"])
    pipeline_id = str(execution["pipeline_id"])
    logical_date = _logical_date(execution)
    current = _current(warehouse, settings, execution_id, pipeline_id, "records_processed")
    if current is None:
        return []

    metadata = execution.get("execution_metadata") or "{}"
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    if isinstance(metadata, dict) and metadata.get("data_date") not in {
        None,
        logical_date.isoformat(),
    }:
        return []  # stale data is owned by the freshness rule, not by volume

    history = _history(warehouse, settings, execution_id, pipeline_id, "records_processed", 120)
    if not history:
        return []
    observed = float(current["value"])
    same_weekday = [
        float(item["value"])
        for item in history
        if item["observed_at"].weekday() == logical_date.weekday()
    ][:8]

    if len(same_weekday) >= 8:
        method = "robust_z_weekday"
        baseline = same_weekday
        centre = median(baseline)
        scale = scale_from_mad(baseline)
        z = robust_z(observed, baseline)
        ratio = observed / centre if centre > 0 else 1.0
        level = max_level(from_z(z), from_ratio(ratio))
        spread = 3.5 * scale
        score = z
        confidence = _confidence(len(baseline), z)
        note: dict[str, Any] = {"tier": "full", "baseline_values": baseline}
    elif len(same_weekday) >= 3:
        method = "weekday_median_short"
        baseline = same_weekday
        centre = median(baseline)
        ratio = observed / centre if centre > 0 else 1.0
        if 0.70 <= ratio <= 1.30:
            return []
        level = from_ratio(ratio)
        spread = centre * 0.20
        score = ratio
        confidence = round(min(0.8, 0.5 + 0.05 * len(baseline)), 3)
        note = {"tier": "short_weekday", "baseline_values": baseline}
    else:
        baseline = [float(item["value"]) for item in history[:7]]
        if len(baseline) < 5:
            return []
        method = "rolling_median_short"
        centre = median(baseline)
        ratio = observed / centre if centre > 0 else 1.0
        if 0.60 <= ratio <= 1.60:
            return []
        level = from_ratio(ratio)
        spread = centre * 0.25
        score = ratio
        confidence = 0.55
        note = {"tier": "rolling", "baseline_values": baseline}

    if level is Severity.INFO:
        return []
    persistent = _persistent(warehouse, settings, pipeline_id, "records_processed", logical_date)
    level = apply_modifiers(level, criticality=criticality, persistent=persistent)
    window_start, window_end = _window(history, current)
    note.update({"persistent": persistent, "ratio_to_expected": round(ratio, 4)})
    return [
        AnomalyRecord(
            entity_type="pipeline",
            entity_id=pipeline_id,
            metric_name="records_processed",
            execution_id=execution_id,
            detected_at=at_noon(logical_date),
            window_start=window_start,
            window_end=window_end,
            observed_value=observed,
            expected_value=centre,
            lower_bound=centre - spread,
            upper_bound=centre + spread,
            score=round(score, 4),
            method=method,
            sample_size=len(baseline),
            severity=level,
            confidence=confidence,
            signal_type=SignalType.STATISTICAL_ANOMALY,
            explanation=note,
        )
    ]


def _detect_duration(
    warehouse: Warehouse,
    settings: Settings,
    execution: Mapping[str, Any],
    criticality: str,
) -> list[AnomalyRecord]:
    execution_id = str(execution["execution_id"])
    pipeline_id = str(execution["pipeline_id"])
    logical_date = _logical_date(execution)
    current = _current(warehouse, settings, execution_id, pipeline_id, "duration_seconds")
    if current is None:
        return []
    history = _history(
        warehouse, settings, execution_id, pipeline_id, "duration_seconds", 30, only_succeeded=True
    )
    baseline = [math.log1p(float(item["value"])) for item in history[:14]]
    if len(baseline) < 7:
        return []

    observed = float(current["value"])
    transformed = math.log1p(observed)
    centre = median(baseline)
    scale = scale_from_mad(baseline)
    z = robust_z(transformed, baseline)
    expected = math.expm1(centre)
    ratio = observed / expected if expected > 0 else 1.0
    if observed < expected * 1.5 or observed - expected < 1.0:
        return []
    level = max_level(from_z(z), from_ratio(ratio))
    if level is Severity.INFO:
        return []

    persistent = _persistent(warehouse, settings, pipeline_id, "duration_seconds", logical_date)
    level = apply_modifiers(level, criticality=criticality, persistent=persistent)
    window_start, window_end = _window(history, current)
    return [
        AnomalyRecord(
            entity_type="pipeline",
            entity_id=pipeline_id,
            metric_name="duration_seconds",
            execution_id=execution_id,
            detected_at=at_noon(logical_date),
            window_start=window_start,
            window_end=window_end,
            observed_value=observed,
            expected_value=expected,
            lower_bound=math.expm1(centre - 3.5 * scale),
            upper_bound=math.expm1(centre + 3.5 * scale),
            score=round(z, 4),
            method="robust_z_log_duration",
            sample_size=len(baseline),
            severity=level,
            confidence=_confidence(len(baseline), z),
            signal_type=SignalType.STATISTICAL_ANOMALY,
            explanation={
                "ratio_to_expected": round(ratio, 4),
                "persistent": persistent,
                "baseline_values": [round(math.expm1(value), 3) for value in baseline],
            },
        )
    ]


def _detect_rate_shift(
    warehouse: Warehouse,
    settings: Settings,
    execution: Mapping[str, Any],
    metric_name: str,
    entity_id: str,
    criticality: str,
) -> list[AnomalyRecord]:
    execution_id = str(execution["execution_id"])
    logical_date = _logical_date(execution)
    current = _current(warehouse, settings, execution_id, entity_id, metric_name)
    if current is None:
        return []
    dimensions = current["dimensions"]
    sample = float(dimensions.get("sample_size", 0))
    failures = float(dimensions.get("failures", 0))
    if sample < MIN_SAMPLE:
        return []

    history = _history(warehouse, settings, execution_id, entity_id, metric_name, 8)[:7]
    pairs = [
        (
            float(item["dimensions"].get("failures", 0)),
            float(item["dimensions"].get("sample_size", 0)),
        )
        for item in history
    ]
    pairs = [(failures_b, sample_b) for failures_b, sample_b in pairs if sample_b > 0]
    if len(pairs) < 3:
        return []

    baseline_failures = sum(failures_b for failures_b, _ in pairs)
    baseline_sample = sum(sample_b for _, sample_b in pairs)
    z, pooled, standard_error = two_proportion_z(
        failures, sample, baseline_failures, baseline_sample
    )
    delta = failures / sample - pooled
    floor = _RATE_FLOORS[metric_name]
    if abs(z) < 3.0 or abs(delta) < floor:
        return []

    level = from_pp(delta)
    persistent = _persistent(warehouse, settings, entity_id, metric_name, logical_date)
    level = apply_modifiers(level, criticality=criticality, persistent=persistent)
    window_start, window_end = _window(history, current)
    return [
        AnomalyRecord(
            entity_type=(
                "pipeline"
                if metric_name == "rejection_rate"
                else "column"
                if metric_name.startswith("null_rate")
                else "dataset"
            ),
            entity_id=entity_id,
            metric_name=metric_name,
            execution_id=execution_id,
            detected_at=at_noon(logical_date),
            window_start=window_start,
            window_end=window_end,
            observed_value=round(failures / sample, 6),
            expected_value=round(pooled, 6),
            lower_bound=round(max(0.0, pooled - 3.0 * standard_error), 6),
            upper_bound=round(pooled + 3.0 * standard_error, 6),
            score=round(z, 4),
            method="rate_shift",
            sample_size=int(sample),
            severity=level,
            confidence=_confidence(len(pairs), z),
            signal_type=SignalType.STATISTICAL_ANOMALY,
            explanation={
                "delta_pp": round(delta, 6),
                "baseline_sample": int(baseline_sample),
                "baseline_failures": int(baseline_failures),
                "persistent": persistent,
            },
        )
    ]


def _detect_psi(
    warehouse: Warehouse,
    settings: Settings,
    execution: Mapping[str, Any],
    criticality: str,
    clean_key: str,
) -> list[AnomalyRecord]:
    execution_id = str(execution["execution_id"])
    logical_date = _logical_date(execution)
    column_key = f"{clean_key}.order_total"
    current = _current(warehouse, settings, execution_id, column_key, "histogram.order_total")
    if current is None:
        return []
    current_counts = [float(value) for value in current["dimensions"].get("counts", [])]
    history = _history(warehouse, settings, execution_id, column_key, "histogram.order_total", 31)[
        :30
    ]
    if len(history) < 7 or not current_counts:
        return []

    width = len(current_counts)
    baseline_counts = [0.0 for _ in range(width)]
    for item in history:
        counts = item["dimensions"].get("counts", [])
        if len(counts) != width:
            continue
        for index, value in enumerate(counts):
            baseline_counts[index] += float(value)
    if sum(baseline_counts) <= 0:
        return []

    value = psi(current_counts, baseline_counts)
    level = from_psi(value)
    if level is Severity.INFO:
        return []
    persistent = _persistent(warehouse, settings, column_key, "psi.order_total", logical_date)
    level = apply_modifiers(level, criticality=criticality, persistent=persistent)
    window_start, window_end = _window(history, current)
    return [
        AnomalyRecord(
            entity_type="column",
            entity_id=column_key,
            metric_name="psi.order_total",
            execution_id=execution_id,
            detected_at=at_noon(logical_date),
            window_start=window_start,
            window_end=window_end,
            observed_value=round(value, 6),
            expected_value=0.0,
            lower_bound=0.0,
            upper_bound=0.10,
            score=round(value, 6),
            method="psi",
            sample_size=len(history),
            severity=level,
            confidence=_confidence(len(history), value),
            signal_type=SignalType.STATISTICAL_ANOMALY,
            explanation={
                "edges": current["dimensions"].get("edges", []),
                "current_counts": current_counts,
                "baseline_counts": baseline_counts,
                "persistent": persistent,
            },
        )
    ]


def _detect_freshness(
    warehouse: Warehouse,
    settings: Settings,
    execution: Mapping[str, Any],
    criticality: str,
    clean_key: str,
) -> list[AnomalyRecord]:
    execution_id = str(execution["execution_id"])
    logical_date = _logical_date(execution)
    current = _current(warehouse, settings, execution_id, clean_key, "freshness_lag_minutes")
    if current is None:
        return []
    sla_rows = warehouse.query(
        f"SELECT freshness_sla_minutes FROM {settings.bq_meta_dataset}.pipelines "
        "WHERE pipeline_id = ?",
        (execution["pipeline_id"],),
    )
    sla = float(sla_rows[0]["freshness_sla_minutes"]) if sla_rows else 1560.0
    observed = float(current["value"])
    if observed > sla:
        return []  # the deterministic freshness rule owns SLA breaches
    history = _history(warehouse, settings, execution_id, clean_key, "freshness_lag_minutes", 14)
    baseline = [float(item["value"]) for item in history[:14]]
    if len(baseline) < 3:
        return []
    z = robust_z(observed, baseline)
    if abs(z) < 3.5:
        return []
    level = apply_modifiers(from_z(z), criticality=criticality)
    window_start, window_end = _window(history, current)
    return [
        AnomalyRecord(
            entity_type="dataset",
            entity_id=clean_key,
            metric_name="freshness_lag_minutes",
            execution_id=execution_id,
            detected_at=at_noon(logical_date),
            window_start=window_start,
            window_end=window_end,
            observed_value=observed,
            expected_value=median(baseline),
            lower_bound=min(baseline),
            upper_bound=max(baseline),
            score=round(z, 4),
            method="robust_z_freshness",
            sample_size=len(baseline),
            severity=level,
            confidence=_confidence(len(baseline), z),
            signal_type=SignalType.STATISTICAL_ANOMALY,
            explanation={"sla_minutes": sla},
        )
    ]


def _detect_failure_streak(
    warehouse: Warehouse,
    settings: Settings,
    execution: Mapping[str, Any],
    criticality: str,
) -> list[AnomalyRecord]:
    logical_date = _logical_date(execution)
    rows = warehouse.query(
        f"""
        SELECT logical_date, status
        FROM {settings.bq_obs_dataset}.pipeline_executions
        WHERE pipeline_id = ?
        ORDER BY logical_date DESC
        LIMIT 6
        """,
        (execution["pipeline_id"],),
    )
    streak = 0
    for row in rows:
        if row["status"] == "failed":
            streak += 1
        else:
            break
    if streak < 2:
        return []
    level = apply_modifiers(from_streak(streak), criticality=criticality)
    return [
        AnomalyRecord(
            entity_type="pipeline",
            entity_id=str(execution["pipeline_id"]),
            metric_name="failure_streak",
            execution_id=str(execution["execution_id"]),
            detected_at=at_noon(logical_date),
            window_start=at_noon(logical_date - timedelta(days=streak)),
            window_end=at_noon(logical_date),
            observed_value=float(streak),
            expected_value=0.0,
            lower_bound=0.0,
            upper_bound=1.0,
            score=float(streak),
            method="failure_streak",
            sample_size=len(rows),
            severity=level,
            confidence=min(0.95, 0.6 + 0.1 * streak),
            signal_type=SignalType.OPERATIONAL_FAILURE,
            explanation={"consecutive_failures": streak},
        )
    ]


def _detect_schema(
    warehouse: Warehouse,
    settings: Settings,
    execution: Mapping[str, Any],
    criticality: str,
    raw_key: str,
) -> list[AnomalyRecord]:
    logical_date = _logical_date(execution)
    rows = warehouse.query(
        f"""
        SELECT column_name, ordinal_position
        FROM {settings.bq_obs_dataset}.schema_snapshots
        WHERE dataset_key = ? AND execution_id = ?
        ORDER BY ordinal_position
        """,
        (raw_key, execution["execution_id"]),
    )
    if not rows:
        return []
    actual = [str(row["column_name"]) for row in rows]
    missing = [column for column in CONTRACT_COLUMNS if column not in actual]
    unexpected = [column for column in actual if column not in CONTRACT_COLUMNS]
    if not missing and not unexpected:
        return []
    level = Severity.HIGH if missing else Severity.LOW
    level = apply_modifiers(level, criticality=criticality)
    return [
        AnomalyRecord(
            entity_type="dataset",
            entity_id=raw_key,
            metric_name="schema_change",
            execution_id=str(execution["execution_id"]),
            detected_at=at_noon(logical_date),
            window_start=at_noon(logical_date),
            window_end=at_noon(logical_date),
            observed_value=float(len(actual)),
            expected_value=float(len(CONTRACT_COLUMNS)),
            lower_bound=float(len(CONTRACT_COLUMNS)),
            upper_bound=float(len(CONTRACT_COLUMNS)),
            score=float(len(missing) + len(unexpected)),
            method="schema_diff",
            sample_size=len(actual),
            severity=level,
            confidence=1.0,
            signal_type=SignalType.SCHEMA_CHANGE,
            explanation={
                "missing": missing,
                "unexpected": unexpected,
                "classification": "breaking" if missing else "additive",
                "actual_columns": actual,
            },
        )
    ]


def _logical_date(execution: Mapping[str, Any]) -> date:
    value = execution["logical_date"]
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _confidence(sample_size: int, magnitude: float) -> float:
    base = min(0.95, 0.5 + 0.05 * sample_size)
    strength = min(1.0, abs(magnitude) / 8.0)
    return round(min(0.95, base * (0.7 + 0.3 * strength)), 3)


__all__ = ["detect_all"]
