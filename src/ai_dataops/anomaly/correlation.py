"""Correlate signals into incidents.

Correlation is evidence-based and deliberately modest: signals are grouped by
execution, entity and a daily bucket, then the incident carries ranked candidate
explanations. The platform never asserts a root cause (ADR-015).
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time
from typing import Any

from ai_dataops.config import Settings
from ai_dataops.domain.enums import Severity, SignalType
from ai_dataops.domain.models import Signal
from ai_dataops.domain.ports import Warehouse
from ai_dataops.ids import deterministic_id
from ai_dataops.timeutil import utcnow

_SEVERITY_ORDER = (
    Severity.INFO,
    Severity.LOW,
    Severity.MEDIUM,
    Severity.HIGH,
    Severity.CRITICAL,
)

_LABELS = {
    SignalType.OPERATIONAL_FAILURE: "execution failure",
    SignalType.RULE_VIOLATION: "rule violations",
    SignalType.SCHEMA_CHANGE: "schema change",
    SignalType.STATISTICAL_ANOMALY: "metric anomalies",
}

_HYPOTHESES: dict[str, dict[str, Any]] = {
    "schema_contract_change": {
        "cause": "Upstream contract change (renamed or removed column)",
        "confidence": 0.7,
        "verification_steps": [
            "Compare the producer schema with the agreed contract",
            "Check the upstream release history for the affected window",
        ],
    },
    "upstream_outage": {
        "cause": "Upstream extract or dependency outage",
        "confidence": 0.65,
        "verification_steps": [
            "Check the upstream service status for the execution window",
            "Inspect the extract error and retry history",
        ],
    },
    "partial_load": {
        "cause": "Partial upstream load or truncated extract",
        "confidence": 0.6,
        "verification_steps": [
            "Compare the source manifest row count with the landing file",
            "Inspect extract logs for timeouts or pagination errors",
        ],
    },
    "failed_run_downstream": {
        "cause": "Failed run leaving downstream datasets stale or incomplete",
        "confidence": 0.55,
        "verification_steps": [
            "Check the failed execution error code and when it started",
            "Confirm whether downstream partitions were written",
        ],
    },
    "distribution_shift": {
        "cause": "Upstream distribution or pricing shift",
        "confidence": 0.4,
        "verification_steps": [
            "Compare the price distribution by category with the previous week",
            "Check for promotions or catalog changes",
        ],
    },
    "replay_or_fanout": {
        "cause": "Replay without idempotency or join fan-out",
        "confidence": 0.45,
        "verification_steps": [
            "Look for a re-run of the same logical date",
            "Check join cardinality on the order key",
        ],
    },
    "stale_delivery": {
        "cause": "Upstream delivery or scheduling delay leaving the dataset stale",
        "confidence": 0.6,
        "verification_steps": [
            "Check the producer schedule and the landing file timestamp",
            "Confirm whether the run processed the expected partition date",
        ],
    },
    "null_introduction": {
        "cause": "Upstream contract change or partial record population introducing nulls",
        "confidence": 0.55,
        "verification_steps": [
            "Compare the null rate by source file and by upstream page",
            "Check recent producer releases for nullable or renamed fields",
        ],
    },
    "volume_shortfall": {
        "cause": "Partial upstream load or volume reduction",
        "confidence": 0.6,
        "verification_steps": [
            "Compare the source manifest row count with the landing file",
            "Check whether the extract stopped early or applied a new filter",
        ],
    },
    "resource_or_slow": {
        "cause": "Resource contention or runtime regression",
        "confidence": 0.55,
        "verification_steps": [
            "Compare runtime with the same weekday over the last four weeks",
            "Check concurrent workloads and query plans for the window",
        ],
    },
    "pipeline_logic": {
        "cause": "Pipeline transformation logic or join change",
        "confidence": 0.5,
        "verification_steps": [
            "Diff the transformation SQL against the last release",
            "Recompute the affected metric on the previous partition",
        ],
    },
    "benign_change": {
        "cause": "Expected business change or a benign one-off variation",
        "confidence": 0.25,
        "verification_steps": [
            "Check the expected-change calendar",
            "Confirm the signal does not persist on the next run",
        ],
    },
}


def correlate(
    warehouse: Warehouse,
    settings: Settings,
    logical_date: date,
    *,
    pipeline_id: str = "sales_daily",
) -> str | None:
    signals = _collect_signals(warehouse, settings, logical_date, pipeline_id)
    if not signals:
        return None

    severity = max((signal.severity for signal in signals), key=_SEVERITY_ORDER.index)
    if len(signals) >= 3:
        severity = _bump(severity)

    signature = hashlib.sha256(
        "|".join(
            [
                *sorted({signal.signal_type.value for signal in signals}),
                pipeline_id,
                logical_date.isoformat(),
            ]
        ).encode()
    ).hexdigest()[:16]
    incident_id = deterministic_id("incident", signature, logical_date)

    existing = warehouse.query(
        f"SELECT status, opened_at FROM {settings.bq_detect_dataset}.incidents "
        "WHERE incident_id = ?",
        (incident_id,),
    )
    status = str(existing[0]["status"]) if existing else "open"
    opened_at = existing[0]["opened_at"] if existing else datetime.combine(logical_date, time.min)
    labels = sorted({_LABELS[signal.signal_type] for signal in signals})
    hypotheses = _rank_hypotheses(signals)
    now = utcnow()

    warehouse.write_rows(
        f"{settings.bq_detect_dataset}.incidents",
        [
            {
                "incident_id": incident_id,
                "signature": signature,
                "bucket_start": datetime.combine(logical_date, time.min),
                "title": f"{pipeline_id}: {', '.join(labels)} on {logical_date.isoformat()}",
                "primary_pipeline_id": pipeline_id,
                "primary_dataset_key": _primary_dataset(signals),
                "severity": severity.value,
                "status": status,
                "opened_at": opened_at,
                "closed_at": None,
                "evidence_count": len(signals),
                "has_diagnosis": False,
                "correlation_method": "heuristic_v1",
                "hypotheses_json": hypotheses,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )

    warehouse.write_rows(
        f"{settings.bq_detect_dataset}.incident_events",
        [
            {
                "incident_id": incident_id,
                "event_type": signal.signal_type.value,
                "event_ref_id": signal.ref_id,
                "linked_at": now,
                "weight": 0.9,
                "evidence": {
                    "summary": signal.summary,
                    "occurred_at": signal.occurred_at.isoformat(),
                    "dataset_key": signal.dataset_key,
                },
            }
            for signal in signals
        ],
    )
    return incident_id


def _collect_signals(
    warehouse: Warehouse,
    settings: Settings,
    logical_date: date,
    pipeline_id: str,
) -> list[Signal]:
    signals: list[Signal] = []

    failures = warehouse.query(
        f"""
        SELECT execution_id, error_code
        FROM {settings.bq_obs_dataset}.pipeline_executions
        WHERE pipeline_id = ? AND logical_date = ? AND status = 'failed'
        """,
        (pipeline_id, logical_date),
    )
    for row in failures:
        signals.append(
            Signal(
                signal_type=SignalType.OPERATIONAL_FAILURE,
                ref_id=str(row["execution_id"]),
                entity_id=pipeline_id,
                dataset_key=None,
                severity=Severity.HIGH,
                occurred_at=datetime.combine(logical_date, time.min),
                summary=f"execution failed: {row['error_code'] or 'unknown error'}",
            )
        )

    violations = warehouse.query(
        f"""
        SELECT r.result_id, r.rule_id, r.dataset_key, r.severity
        FROM {settings.bq_quality_dataset}.quality_rule_results r
        JOIN {settings.bq_obs_dataset}.pipeline_executions e
          ON e.execution_id = r.execution_id
        WHERE e.pipeline_id = ? AND e.logical_date = ? AND r.status = 'fail'
        """,
        (pipeline_id, logical_date),
    )
    for row in violations:
        signals.append(
            Signal(
                signal_type=SignalType.RULE_VIOLATION,
                ref_id=str(row["result_id"]),
                entity_id=str(row["dataset_key"]),
                dataset_key=str(row["dataset_key"]),
                severity=Severity(str(row["severity"])),
                occurred_at=datetime.combine(logical_date, time.min),
                summary=f"rule failed: {row['rule_id']}",
            )
        )

    anomalies = warehouse.query(
        f"""
        SELECT anomaly_id, entity_id, metric_name, severity, signal_type
        FROM {settings.bq_detect_dataset}.anomalies
        WHERE CAST(detected_at AS DATE) = ?
        """,
        (logical_date,),
    )
    for row in anomalies:
        entity_id = str(row["entity_id"])
        signals.append(
            Signal(
                signal_type=SignalType(str(row["signal_type"])),
                ref_id=str(row["anomaly_id"]),
                entity_id=entity_id,
                dataset_key=entity_id if "." in entity_id else None,
                severity=Severity(str(row["severity"])),
                occurred_at=datetime.combine(logical_date, time.min),
                summary=f"{row['metric_name']} anomaly on {entity_id}",
            )
        )

    return signals


def _primary_dataset(signals: list[Signal]) -> str | None:
    for signal in signals:
        if signal.dataset_key:
            return signal.dataset_key
    return None


def _bump(level: Severity) -> Severity:
    index = min(len(_SEVERITY_ORDER) - 1, _SEVERITY_ORDER.index(level) + 1)
    return _SEVERITY_ORDER[index]


def _rank_hypotheses(signals: list[Signal]) -> list[dict[str, Any]]:
    types = {signal.signal_type for signal in signals}
    metrics = {signal.summary.split(" anomaly")[0] for signal in signals}
    evidence = [signal.ref_id for signal in signals]
    keys: list[str] = []
    if SignalType.SCHEMA_CHANGE in types:
        keys.append("schema_contract_change")
    if SignalType.OPERATIONAL_FAILURE in types:
        keys.append("upstream_outage")
    if SignalType.OPERATIONAL_FAILURE in types and SignalType.STATISTICAL_ANOMALY in types:
        keys.append("partial_load")
    if SignalType.OPERATIONAL_FAILURE in types and SignalType.RULE_VIOLATION in types:
        keys.append("failed_run_downstream")
    if any(metric.startswith("psi.") for metric in metrics):
        keys.append("distribution_shift")
    if any(metric == "duplicate_rate" for metric in metrics):
        keys.append("replay_or_fanout")
    summaries = " ".join(signal.summary for signal in signals).lower()
    if "freshness" in summaries:
        keys.append("stale_delivery")
    if "completeness" in summaries:
        keys.append("null_introduction")
    if "consistency" in summaries:
        keys.append("pipeline_logic")
    if any(metric in {"records_processed", "row_count"} for metric in metrics):
        keys.append("volume_shortfall")
    if any(metric == "duration_seconds" for metric in metrics):
        keys.append("resource_or_slow")
    if not keys:
        keys.append("benign_change")

    hypotheses = []
    for key in keys[:3]:
        template = _HYPOTHESES[key]
        hypotheses.append(
            {
                "cause": template["cause"],
                "confidence": template["confidence"],
                "evidence_ids": evidence,
                "verification_steps": template["verification_steps"],
            }
        )
    return hypotheses


__all__ = ["correlate"]
