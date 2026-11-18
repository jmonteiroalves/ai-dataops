"""Serving snapshot export for the public dashboard.

The dashboard never queries the warehouse. This exporter turns the serving views into a
versioned JSON document; the Streamlit app reads only that file (ADR-007).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from ai_dataops.config import Settings
from ai_dataops.domain.ports import Warehouse
from ai_dataops.timeutil import utcnow


def export_snapshot(
    warehouse: Warehouse,
    settings: Settings,
    *,
    end_date: date | None = None,
    output_dir: Path | None = None,
) -> Path:
    latest = warehouse.query(
        f"SELECT MAX(logical_date) AS latest FROM {settings.bq_obs_dataset}.pipeline_executions"
    )
    resolved_end = end_date or (latest[0]["latest"] if latest and latest[0]["latest"] else None)
    if resolved_end is None:
        raise ValueError("no executions available to export")

    executions = warehouse.query(
        f"""
        SELECT execution_id, logical_date, pipeline_id, status, scenario_id, duration_seconds,
               records_read, records_processed, records_rejected, rejection_rate, error_code
        FROM {settings.bq_obs_dataset}.pipeline_executions
        WHERE logical_date <= ?
        ORDER BY logical_date DESC
        LIMIT 60
        """,
        (resolved_end,),
    )
    latest_execution = executions[0] if executions else {}
    latest_execution_id = warehouse.query(
        f"SELECT execution_id FROM {settings.bq_obs_dataset}.pipeline_executions "
        "ORDER BY logical_date DESC, attempt DESC LIMIT 1"
    )
    execution_id = latest_execution_id[0]["execution_id"] if latest_execution_id else None

    quality_scores = (
        warehouse.query(
            f"""
            SELECT qs.dataset_key, qs.dimension, qs.score, qs.rules_total,
                   qs.rules_passed, qs.rules_failed, qs.rules_error
            FROM {settings.bq_quality_dataset}.quality_scores qs
            WHERE qs.execution_id = ?
            ORDER BY qs.dataset_key, qs.dimension
            """,
            (execution_id,),
        )
        if execution_id
        else []
    )
    quality_trend = warehouse.query(
        f"""
        SELECT e.logical_date, AVG(qs.score) AS score
        FROM {settings.bq_quality_dataset}.quality_scores qs
        JOIN {settings.bq_obs_dataset}.pipeline_executions e
          ON e.execution_id = qs.execution_id
        WHERE e.logical_date <= ? AND qs.score IS NOT NULL
        GROUP BY e.logical_date
        ORDER BY e.logical_date
        """,
        (resolved_end,),
    )
    failing_rules = warehouse.query(
        f"""
        SELECT r.result_id, r.rule_id, q.name, q.dataset_key, q.dimension, r.status,
               r.observed_value, r.threshold, r.severity, e.logical_date
        FROM {settings.bq_quality_dataset}.quality_rule_results r
        JOIN {settings.bq_meta_dataset}.quality_rules q ON q.rule_id = r.rule_id
        JOIN {settings.bq_obs_dataset}.pipeline_executions e
          ON e.execution_id = r.execution_id
        WHERE r.status IN ('fail', 'error') AND e.logical_date <= ?
        ORDER BY e.logical_date DESC
        LIMIT 40
        """,
        (resolved_end,),
    )
    anomalies = warehouse.query(
        f"""
        SELECT anomaly_id, detected_at, entity_id, metric_name, observed_value, expected_value,
               lower_bound, upper_bound, score, method, severity, confidence, status
        FROM {settings.bq_detect_dataset}.anomalies
        WHERE CAST(detected_at AS DATE) <= ?
        ORDER BY detected_at DESC
        LIMIT 120
        """,
        (resolved_end,),
    )
    incidents = warehouse.query(
        f"""
        SELECT * FROM {settings.bq_detect_dataset}.incidents
        WHERE CAST(bucket_start AS DATE) <= ?
        ORDER BY bucket_start DESC
        LIMIT 60
        """,
        (resolved_end,),
    )
    diagnoses = warehouse.query(
        f"""
        SELECT * FROM {settings.bq_ai_dataset}.diagnoses
        WHERE is_current = TRUE AND status = 'success'
        ORDER BY responded_at DESC
        LIMIT 60
        """
    )
    ai_requests = warehouse.query(
        f"""
        SELECT CAST(created_at AS DATE) AS day, COUNT(*) AS requests,
               COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(estimated_cost_usd), 0) AS cost_usd
        FROM {settings.bq_ai_dataset}.ai_requests
        GROUP BY 1
        ORDER BY 1 DESC
        LIMIT 30
        """
    )
    invalid_diagnoses = warehouse.query(
        f"SELECT COUNT(*) AS n FROM {settings.bq_ai_dataset}.diagnoses "
        "WHERE status IN ('invalid', 'failed')"
    )[0]["n"]

    incident_ids = [str(item["incident_id"]) for item in incidents]
    raw_events = (
        warehouse.query(
            f"""
            SELECT incident_id, event_type, event_ref_id
            FROM {settings.bq_detect_dataset}.incident_events
            WHERE incident_id IN ({", ".join("?" for _ in incident_ids)})
            """,
            tuple(incident_ids),
        )
        if incident_ids
        else []
    )
    anomalies_by_id = {str(item["anomaly_id"]): item for item in anomalies}
    rules_by_id = {str(item["result_id"]): item for item in failing_rules}
    executions_by_id = {str(item["execution_id"]): item for item in executions}
    events = [
        _enrich_event(event, anomalies_by_id, rules_by_id, executions_by_id) for event in raw_events
    ]
    recommendations = (
        warehouse.query(
            f"""
            SELECT * FROM {settings.bq_ai_dataset}.recommendations
            WHERE incident_id IN ({", ".join("?" for _ in incident_ids)})
            ORDER BY created_at DESC
            """,
            tuple(incident_ids),
        )
        if incident_ids
        else []
    )

    latest_diagnosis_by_incident: dict[str, dict[str, Any]] = {}
    for diagnosis in diagnoses:
        latest_diagnosis_by_incident.setdefault(str(diagnosis["incident_id"]), diagnosis)

    incident_lookup = {str(item["incident_id"]): item for item in incidents}
    for diagnosis in diagnoses:
        incident = incident_lookup.get(str(diagnosis.get("incident_id")))
        if incident:
            diagnosis["incident_date"] = str(incident.get("bucket_start"))[:10]
            diagnosis["incident_title"] = str(incident.get("title"))

    incident_details = []
    for incident in incidents:
        incident_id = str(incident["incident_id"])
        incident_details.append(
            {
                "incident": incident,
                "events": [item for item in events if item["incident_id"] == incident_id],
                "diagnosis": latest_diagnosis_by_incident.get(incident_id),
                "recommendations": [
                    item for item in recommendations if item["incident_id"] == incident_id
                ],
            }
        )

    open_incidents = warehouse.query(
        f"SELECT COUNT(*) AS n FROM {settings.bq_detect_dataset}.incidents "
        "WHERE status IN ('open', 'investigating')"
    )[0]["n"]
    pipelines = warehouse.query(
        f"SELECT COUNT(*) AS n FROM {settings.bq_meta_dataset}.pipelines WHERE is_active = TRUE"
    )[0]["n"]
    latest_status = str(latest_execution.get("status", "unknown"))
    healthy = pipelines if latest_status == "succeeded" else max(0, pipelines - 1)
    average_quality = (
        sum(float(item["score"]) for item in quality_scores if item["score"] is not None)
        / max(1, sum(1 for item in quality_scores if item["score"] is not None))
        if quality_scores
        else None
    )

    snapshot: dict[str, Any] = {
        "manifest": {
            "generated_at": utcnow().isoformat() + "Z",
            "end_date": str(resolved_end),
            "engine": settings.engine.value,
            "counts": {
                "executions": len(executions),
                "anomalies": len(anomalies),
                "incidents": len(incidents),
                "diagnoses": len(diagnoses),
                "quality_results": len(quality_scores),
            },
        },
        "overview": {
            "pipelines_monitored": pipelines,
            "pipelines_healthy": healthy,
            "pipelines_degraded": max(0, pipelines - healthy),
            "open_incidents": open_incidents,
            "average_quality_score": round(average_quality, 4)
            if average_quality is not None
            else None,
            "last_run_date": str(latest_execution.get("logical_date", resolved_end)),
            "last_run_status": latest_status,
            "total_cost_usd": round(sum(float(item["cost_usd"]) for item in ai_requests), 6),
        },
        "executions": executions,
        "quality": {
            "scores": quality_scores,
            "trend": quality_trend,
            "failing_rules": failing_rules,
        },
        "anomalies": anomalies,
        "incidents": incident_details,
        "diagnoses": diagnoses,
        "operations": {
            "daily": ai_requests,
            "invalid_diagnoses": invalid_diagnoses,
        },
    }

    destination = (output_dir or settings.serving_dir) / "snapshot.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    return destination


def _enrich_event(
    event: dict[str, Any],
    anomalies: dict[str, dict[str, Any]],
    rules: dict[str, dict[str, Any]],
    executions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    enriched = dict(event)
    event_type = str(event.get("event_type"))
    ref_id = str(event.get("event_ref_id"))
    if event_type in {"statistical_anomaly", "schema_change"}:
        anomaly = anomalies.get(ref_id)
        if anomaly:
            enriched.update(
                label=str(anomaly.get("metric_name")),
                detail=(
                    f"{_number(anomaly.get('observed_value'))} observed, "
                    f"{_number(anomaly.get('expected_value'))} expected"
                ),
                severity=str(anomaly.get("severity")),
            )
    elif event_type == "rule_violation":
        rule = rules.get(ref_id)
        if rule:
            enriched.update(
                label=str(rule.get("rule_id")),
                detail=(
                    f"{rule.get('dimension')} · observed {_number(rule.get('observed_value'))}"
                ),
                severity=str(rule.get("severity")),
            )
    elif event_type == "operational_failure":
        execution = executions.get(ref_id)
        if execution:
            enriched.update(
                label=str(execution.get("error_code") or "execution failed"),
                detail=(
                    f"{execution.get('logical_date')} · "
                    f"{_number(execution.get('records_processed'))} processed"
                ),
                severity="high",
            )
    return enriched


def _number(value: object) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value) if isinstance(value, (int, float)) else float(str(value))
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}"


__all__ = ["export_snapshot"]
