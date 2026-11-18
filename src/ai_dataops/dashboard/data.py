"""Read-only data access for the dashboard.

Pure functions over the serving snapshot: no warehouse, no Streamlit. This is what the tests
exercise, so the UI can stay thin and the page logic remains deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SNAPSHOT_NAME = "snapshot.json"


def load_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"serving snapshot not found: {path}")
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def load_snapshot_uri(uri: str) -> dict[str, Any]:
    if uri.startswith("gs://"):
        from ai_dataops.adapters.gcs import download_text

        loaded: dict[str, Any] = json.loads(download_text(uri))
        return loaded
    return load_snapshot(Path(uri))


def overview_cards(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    overview = snapshot.get("overview", {})
    quality = overview.get("average_quality_score")
    return [
        {"label": "Pipelines monitored", "value": str(overview.get("pipelines_monitored", 0))},
        {"label": "Healthy", "value": str(overview.get("pipelines_healthy", 0))},
        {"label": "Degraded", "value": str(overview.get("pipelines_degraded", 0))},
        {"label": "Open incidents", "value": str(overview.get("open_incidents", 0))},
        {
            "label": "Quality score",
            "value": f"{quality:.1%}" if isinstance(quality, (int, float)) else "n/a",
        },
        {
            "label": "AI spend",
            "value": f"US$ {float(overview.get('total_cost_usd', 0.0)):.2f}",
        },
    ]


def execution_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in snapshot.get("executions", []):
        rows.append(
            {
                "date": item.get("logical_date"),
                "status": item.get("status"),
                "scenario": item.get("scenario_id"),
                "processed": item.get("records_processed"),
                "rejected": item.get("records_rejected"),
                "duration_s": item.get("duration_seconds"),
                "error": item.get("error_code"),
            }
        )
    return rows


def quality_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in snapshot.get("quality", {}).get("scores", []):
        score = item.get("score")
        rows.append(
            {
                "dataset": item.get("dataset_key"),
                "dimension": item.get("dimension"),
                "score": round(float(score), 4) if score is not None else None,
                "passed": item.get("rules_passed"),
                "failed": item.get("rules_failed"),
                "errors": item.get("rules_error"),
            }
        )
    return rows


def quality_trend(snapshot: dict[str, Any]) -> dict[str, list[float]]:
    trend = snapshot.get("quality", {}).get("trend", [])
    return {
        "score": [float(item["score"]) if item.get("score") is not None else 0.0 for item in trend],
    }


def failing_rule_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "date": item.get("logical_date"),
            "rule": item.get("rule_id"),
            "dimension": item.get("dimension"),
            "observed": item.get("observed_value"),
            "threshold": item.get("threshold"),
            "severity": item.get("severity"),
        }
        for item in snapshot.get("quality", {}).get("failing_rules", [])
    ]


def anomaly_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "detected_at": item.get("detected_at"),
            "entity": item.get("entity_id"),
            "metric": item.get("metric_name"),
            "observed": item.get("observed_value"),
            "expected": item.get("expected_value"),
            "method": item.get("method"),
            "severity": item.get("severity"),
            "status": item.get("status"),
        }
        for item in snapshot.get("anomalies", [])
    ]


def incident_choices(snapshot: dict[str, Any]) -> list[tuple[str, str]]:
    choices = []
    for detail in snapshot.get("incidents", []):
        incident = detail.get("incident", {})
        label = (
            f"{incident.get('bucket_start', '')} · {incident.get('severity', '')} · "
            f"{incident.get('title', '')}"
        )
        choices.append((label, str(incident.get("incident_id"))))
    return choices


def incident_detail(snapshot: dict[str, Any], incident_id: str) -> dict[str, Any] | None:
    for detail in snapshot.get("incidents", []):
        if str(detail.get("incident", {}).get("incident_id")) == incident_id:
            result: dict[str, Any] = dict(detail)
            return result
    return None


def parse_json_field(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


def diagnosis_view(detail: dict[str, Any]) -> dict[str, Any] | None:
    diagnosis = detail.get("diagnosis")
    if not diagnosis:
        return None
    return {
        "summary": diagnosis.get("summary"),
        "facts": parse_json_field(diagnosis.get("observed_facts")) or [],
        "indicators": parse_json_field(diagnosis.get("affected_indicators")) or [],
        "hypotheses": parse_json_field(diagnosis.get("hypotheses")) or [],
        "impact": parse_json_field(diagnosis.get("impact")) or {},
        "investigation": parse_json_field(diagnosis.get("investigation_steps")) or [],
        "actions": parse_json_field(diagnosis.get("suggested_actions")) or [],
        "limitations": parse_json_field(diagnosis.get("limitations")) or [],
        "confidence": diagnosis.get("confidence"),
        "insufficient_evidence": diagnosis.get("insufficient_evidence"),
        "model": diagnosis.get("model_id"),
        "prompt_version": diagnosis.get("prompt_version"),
        "recommendations": detail.get("recommendations", []),
    }


__all__ = [
    "anomaly_rows",
    "diagnosis_view",
    "execution_rows",
    "failing_rule_rows",
    "incident_choices",
    "incident_detail",
    "load_snapshot",
    "load_snapshot_uri",
    "overview_cards",
    "parse_json_field",
    "quality_rows",
    "quality_trend",
]


SEVERITY_TONES = {
    "critical": "critical",
    "high": "warning",
    "medium": "caution",
    "low": "neutral",
    "info": "neutral",
}

DIMENSIONS = (
    "completeness",
    "uniqueness",
    "validity",
    "consistency",
    "integrity",
    "freshness",
    "schema",
)


def snapshot_health(snapshot: dict[str, Any]) -> dict[str, str]:
    """One sentence a person can act on, plus the tone that drives the hero."""
    incidents = [
        item.get("incident", {})
        for item in snapshot.get("incidents", [])
        if item.get("incident", {}).get("status") in {"open", "investigating"}
    ]
    critical = sum(1 for item in incidents if item.get("severity") == "critical")
    high = sum(1 for item in incidents if item.get("severity") == "high")
    overview = snapshot.get("overview", {})
    quality = overview.get("average_quality_score")

    if critical:
        state, tone = "Critical watch", "critical"
    elif high or incidents:
        state, tone = "Attention", "warning"
    else:
        state, tone = "Stable", "healthy"

    run = str(overview.get("last_run_status", "unknown"))
    quality_text = (
        f"{quality:.1%} quality" if isinstance(quality, (int, float)) else "quality not measured"
    )
    if incidents:
        sentence = (
            f"{len(incidents)} incidents are open, {critical} of them critical. "
            f"The latest run {run}, with {quality_text}."
        )
    else:
        sentence = f"No open incidents. The latest run {run}, with {quality_text}."
    return {"state": state, "tone": tone, "sentence": sentence}


def severity_counts(snapshot: dict[str, Any]) -> dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for detail in snapshot.get("incidents", []):
        incident = detail.get("incident", {})
        if incident.get("status") not in {"open", "investigating"}:
            continue
        severity = str(incident.get("severity", "info"))
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def quality_matrix(snapshot: dict[str, Any]) -> dict[str, Any]:
    rows: dict[str, dict[str, float | None]] = {}
    for item in snapshot.get("quality", {}).get("scores", []):
        dataset = str(item.get("dataset_key"))
        rows.setdefault(dataset, {})
        score = item.get("score")
        rows[dataset][str(item.get("dimension"))] = float(score) if score is not None else None
    order = sorted(rows)
    return {
        "datasets": order,
        "dimensions": list(DIMENSIONS),
        "values": rows,
    }


def run_strip(snapshot: dict[str, Any], limit: int = 32) -> list[dict[str, Any]]:
    executions = list(snapshot.get("executions", []))[:limit]
    executions.reverse()
    if not executions:
        return []
    peak = max(float(item.get("records_processed") or 0) for item in executions) or 1.0
    strip = []
    for item in executions:
        processed = float(item.get("records_processed") or 0)
        strip.append(
            {
                "date": str(item.get("logical_date")),
                "status": str(item.get("status")),
                "processed": int(processed),
                "height": 16.0
                if item.get("status") == "failed"
                else round(max(4.0, processed / peak * 100), 1),
                "tone": "failed" if item.get("status") == "failed" else "succeeded",
            }
        )
    return strip


def anomaly_rails(snapshot: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    anomalies = list(snapshot.get("anomalies", []))[:limit]
    if not anomalies:
        return []
    values: list[float] = []
    for item in anomalies:
        for key in ("lower_bound", "upper_bound", "observed_value", "expected_value"):
            value = item.get(key)
            if isinstance(value, (int, float)):
                values.append(float(value))
    low = min(values) if values else 0.0
    high = max(values) if values else 1.0
    span = (high - low) or 1.0

    def position(value: Any) -> float | None:
        if not isinstance(value, (int, float)):
            return None
        return round(max(0.0, min(100.0, (float(value) - low) / span * 100)), 2)

    rails = []
    for item in anomalies:
        rails.append(
            {
                "metric": str(item.get("metric_name")),
                "entity": str(item.get("entity_id")),
                "severity": str(item.get("severity")),
                "tone": SEVERITY_TONES.get(str(item.get("severity")), "neutral"),
                "observed": item.get("observed_value"),
                "expected": item.get("expected_value"),
                "method": str(item.get("method")),
                "observed_pct": position(item.get("observed_value")),
                "expected_pct": position(item.get("expected_value")),
                "low_pct": position(item.get("lower_bound")),
                "high_pct": position(item.get("upper_bound")),
                "detected_at": str(item.get("detected_at")),
            }
        )
    return rails


def format_int(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    return f"{int(value):,}"


def format_ratio(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    return f"{float(value):.1%}"


def format_decimal(value: Any, places: int = 2) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    return f"{float(value):,.{places}f}"


_SEVERITY_WEIGHT = {
    "critical": 3.0,
    "high": 2.0,
    "medium": 1.0,
    "low": 0.5,
    "info": 0.2,
}


def incident_load(snapshot: dict[str, Any]) -> tuple[list[float], list[str]]:
    """Severity-weighted incident count per execution date, for the hero trace."""
    dates = [
        str(item.get("logical_date"))[:10]
        for item in snapshot.get("quality", {}).get("trend", [])
        if item.get("logical_date")
    ]
    by_date: dict[str, float] = {}
    for detail in snapshot.get("incidents", []):
        incident = detail.get("incident", {})
        day = str(incident.get("bucket_start", ""))[:10]
        if not day:
            continue
        weight = _SEVERITY_WEIGHT.get(str(incident.get("severity")), 0.2)
        by_date[day] = by_date.get(day, 0.0) + weight
    return [by_date.get(day, 0.0) for day in dates], dates
