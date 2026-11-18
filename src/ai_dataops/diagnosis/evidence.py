"""Evidence bundle builder.

Design rule: the bundle is the *only* thing the model sees. It contains facts the platform
computed, never the injected demo scenario, raw rows or credentials.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ai_dataops.config import Settings
from ai_dataops.domain.ports import Warehouse
from ai_dataops.errors import NotFoundError

MAX_EXECUTIONS = 7
MAX_SIGNALS = 10
MAX_ERRORS = 5
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    incident_id: str
    bundle: dict[str, Any]
    entries: list[dict[str, str]]
    evidence_ids: set[str]
    canonical_json: str
    evidence_hash: str
    estimated_tokens: int

    def render(self) -> str:
        return json.dumps(self.bundle, indent=2, sort_keys=True, default=str)


def build_evidence(
    warehouse: Warehouse,
    settings: Settings,
    incident_id: str,
) -> EvidenceBundle:
    incident_rows = warehouse.query(
        f"SELECT * FROM {settings.bq_detect_dataset}.incidents WHERE incident_id = ?",
        (incident_id,),
    )
    if not incident_rows:
        raise NotFoundError("incident not found", context={"incident_id": incident_id})
    incident = incident_rows[0]
    logical_date = _as_date(incident["bucket_start"])
    pipeline_id = str(incident["primary_pipeline_id"] or "sales_daily")
    primary_dataset = str(incident["primary_dataset_key"] or f"{settings.demo_dataset}.sales_clean")

    pipeline_rows = warehouse.query(
        f"SELECT * FROM {settings.bq_meta_dataset}.pipelines WHERE pipeline_id = ?",
        (pipeline_id,),
    )
    pipeline = pipeline_rows[0] if pipeline_rows else {}
    dataset_rows = warehouse.query(
        f"""
        SELECT d.* FROM {settings.bq_meta_dataset}.pipeline_datasets pd
        JOIN {settings.bq_meta_dataset}.datasets d ON d.dataset_key = pd.dataset_key
        WHERE pd.pipeline_id = ?
        """,
        (pipeline_id,),
    )
    datasets = {str(row["dataset_key"]): row for row in dataset_rows}
    primary = datasets.get(primary_dataset, {})

    edges = warehouse.query(
        f"""
        SELECT upstream_dataset_key, downstream_dataset_key, relation_type
        FROM {settings.bq_meta_dataset}.dataset_lineage
        WHERE upstream_dataset_key = ? OR downstream_dataset_key = ?
        """,
        (primary_dataset, primary_dataset),
    )
    upstream = [
        str(edge["upstream_dataset_key"])
        for edge in edges
        if str(edge["downstream_dataset_key"]) == primary_dataset
    ]
    downstream = [
        str(edge["downstream_dataset_key"])
        for edge in edges
        if str(edge["upstream_dataset_key"]) == primary_dataset
    ]

    history = warehouse.query(
        f"""
        SELECT * FROM {settings.bq_obs_dataset}.pipeline_executions
        WHERE pipeline_id = ? AND logical_date <= ?
        ORDER BY logical_date DESC, attempt DESC
        LIMIT ?
        """,
        (pipeline_id, logical_date, MAX_EXECUTIONS),
    )
    events = warehouse.query(
        f"SELECT event_type, event_ref_id FROM {settings.bq_detect_dataset}.incident_events "
        "WHERE incident_id = ?",
        (incident_id,),
    )
    anomaly_refs = [
        str(event["event_ref_id"])
        for event in events
        if event["event_type"] in {"statistical_anomaly", "schema_change"}
    ][:MAX_SIGNALS]
    rule_refs = [
        str(event["event_ref_id"]) for event in events if event["event_type"] == "rule_violation"
    ][:MAX_SIGNALS]
    failure_refs = [
        str(event["event_ref_id"])
        for event in events
        if event["event_type"] == "operational_failure"
    ]

    anomalies = _fetch_by_ids(
        warehouse,
        f"{settings.bq_detect_dataset}.anomalies",
        "anomaly_id",
        anomaly_refs,
    )
    rules = (
        warehouse.query(
            f"""
            SELECT r.*, q.dimension
            FROM {settings.bq_quality_dataset}.quality_rule_results r
            JOIN {settings.bq_meta_dataset}.quality_rules q ON q.rule_id = r.rule_id
            WHERE r.result_id IN ({", ".join("?" for _ in rule_refs)})
            """,
            tuple(rule_refs),
        )
        if rule_refs
        else []
    )
    failures = _fetch_by_ids(
        warehouse,
        f"{settings.bq_obs_dataset}.pipeline_executions",
        "execution_id",
        failure_refs,
    )

    entries: list[dict[str, str]] = []
    counter = [0]

    def add(kind: str, ref_id: object, excerpt: str) -> str:
        counter[0] += 1
        evidence_id = f"EV-{counter[0]:02d}"
        entries.append(
            {
                "evidence_id": evidence_id,
                "evidence_type": kind,
                "ref_id": str(ref_id),
                "payload_excerpt": excerpt[:500],
            }
        )
        return evidence_id

    execution_items = []
    for row in history:
        evidence_id = add(
            "execution",
            row["execution_id"],
            f"{row['logical_date']} {row['status']} processed={row['records_processed']}",
        )
        execution_items.append(
            {
                "evidence_id": evidence_id,
                "date": str(row["logical_date"]),
                "status": row["status"],
                "records_read": row["records_read"],
                "records_processed": row["records_processed"],
                "records_rejected": row["records_rejected"],
                "duration_seconds": row["duration_seconds"],
                "error_code": row["error_code"],
            }
        )

    signal_items: list[dict[str, Any]] = []
    schema_diff: dict[str, Any] = {}
    for row in anomalies:
        explanation = _json_object(row["explanation"])
        evidence_id = add(
            "anomaly",
            row["anomaly_id"],
            f"{row['metric_name']} observed={row['observed_value']}",
        )
        signal_items.append(
            {
                "evidence_id": evidence_id,
                "kind": str(row["signal_type"]),
                "metric": row["metric_name"],
                "entity": row["entity_id"],
                "observed": row["observed_value"],
                "expected": row["expected_value"],
                "lower_bound": row["lower_bound"],
                "upper_bound": row["upper_bound"],
                "score": row["score"],
                "method": row["method"],
                "sample_size": row["sample_size"],
                "severity": row["severity"],
                "explanation": explanation,
            }
        )
        if str(row["metric_name"]) == "schema_change" and explanation:
            schema_diff = {
                "missing": explanation.get("missing", []),
                "unexpected": explanation.get("unexpected", []),
                "classification": explanation.get("classification"),
            }

    for row in rules:
        details = _json_object(row["details"])
        evidence_id = add(
            "rule_result",
            row["result_id"],
            f"{row['rule_id']} {row['status']}",
        )
        signal_items.append(
            {
                "evidence_id": evidence_id,
                "kind": "rule_violation",
                "rule_id": row["rule_id"],
                "dimension": row.get("dimension"),
                "observed": row["observed_value"],
                "threshold": row["threshold"],
                "records_evaluated": row["records_evaluated"],
                "records_failed": row["records_failed"],
                "severity": row["severity"],
                "details": details,
                "samples": details.get("samples", []),
            }
        )

    errors: list[dict[str, Any]] = []
    seen_messages: set[str] = set()
    for row in [*failures, *history]:
        message = _safe_text(row["error_message"]) if row["error_message"] else ""
        code = str(row["error_code"] or "")
        if not message and not code:
            continue
        key = f"{code}:{message}"
        if key in seen_messages:
            continue
        seen_messages.add(key)
        evidence_id = add("error", row["execution_id"], message or code)
        errors.append(
            {
                "evidence_id": evidence_id,
                "code": code,
                "message": message,
                "occurred_at": str(row["logical_date"]),
            }
        )
        if len(errors) >= MAX_ERRORS:
            break

    bundle: dict[str, Any] = {
        "bundle_version": "1.0",
        "incident": {
            "id": incident["incident_id"],
            "title": incident["title"],
            "severity": incident["severity"],
            "opened_at": str(incident["opened_at"]),
            "signal_count": incident["evidence_count"],
        },
        "pipeline": {
            "id": pipeline_id,
            "name": pipeline.get("name"),
            "criticality": pipeline.get("criticality"),
            "domain": pipeline.get("domain"),
            "freshness_sla_minutes": pipeline.get("freshness_sla_minutes"),
            "expected_runtime_seconds": pipeline.get("expected_runtime_seconds"),
        },
        "dataset": {
            "key": primary_dataset,
            "layer": primary.get("layer"),
            "criticality": primary.get("criticality"),
            "freshness_sla_minutes": primary.get("freshness_sla_minutes"),
            "monitored_datasets": sorted(datasets),
        },
        "lineage": {"upstream": upstream, "downstream": downstream},
        "executions": execution_items,
        "signals": signal_items,
        "schema_diff": schema_diff,
        "errors": errors,
        "candidate_explanations": _candidates(incident.get("hypotheses_json")),
        "expected_changes": [],
        "untrusted_blocks": ["errors", "signals"],
        "constraints": {
            "schema_version": "diagnosis.v1",
            "max_hypotheses": 3,
            "cite_evidence": True,
            "never_execute": True,
        },
    }

    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"), default=str)
    return EvidenceBundle(
        incident_id=incident_id,
        bundle=bundle,
        entries=entries,
        evidence_ids={entry["evidence_id"] for entry in entries},
        canonical_json=canonical,
        evidence_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        estimated_tokens=max(1, len(canonical) // 4),
    )


def _candidates(raw: object) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            return []
    elif isinstance(raw, list):
        loaded = raw
    else:
        return []
    if not isinstance(loaded, list):
        return []
    return [
        {
            "cause": str(item.get("cause", ""))[:300],
            "confidence": float(item.get("confidence", 0.0)),
            "verification_steps": [str(step)[:200] for step in item.get("verification_steps", [])],
        }
        for item in loaded[:3]
        if isinstance(item, dict)
    ]


def _fetch_by_ids(
    warehouse: Warehouse,
    table: str,
    key: str,
    ids: list[str],
) -> list[dict[str, Any]]:
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    return warehouse.query(f"SELECT * FROM {table} WHERE {key} IN ({placeholders})", tuple(ids))


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _safe_text(value: object, limit: int = 300) -> str:
    text = _CONTROL_CHARS.sub(" ", str(value))
    return " ".join(text.split())[:limit]


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


__all__ = ["EvidenceBundle", "build_evidence"]
