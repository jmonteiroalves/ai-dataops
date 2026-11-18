"""Diagnosis evaluation harness.

Ground truth comes from the scenario labels used to generate the demo. The model never sees
those labels; the evaluation joins them after the fact.

Category matching is a documented proxy: the model writes free text, so the first hypothesis
is classified by keyword hits. The harness reports per-incident rows so a human can audit it.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ai_dataops.config import Settings
from ai_dataops.diagnosis.engine import diagnose_incident
from ai_dataops.diagnosis.provider import DiagnosisProvider
from ai_dataops.domain.ports import Warehouse
from ai_dataops.timeutil import utcnow

SCENARIO_CATEGORY = {
    "ingestion_failure": "upstream_failure",
    "schema_change": "upstream_contract_change",
    "volume_drop": "partial_load",
    "null_spike": "upstream_contract_change",
    "duplicate_spike": "replay_or_fanout",
    "price_anomaly": "upstream_shift",
    "slow_run": "resource_or_slow",
    "stale_data": "freshness_or_schedule",
    "normal": "benign_change",
}

SCENARIO_ACCEPTED = {
    "ingestion_failure": {"upstream_failure"},
    "schema_change": {"upstream_contract_change"},
    "volume_drop": {"partial_load", "upstream_failure"},
    "null_spike": {"upstream_contract_change", "upstream_failure", "pipeline_logic"},
    "duplicate_spike": {"replay_or_fanout", "pipeline_logic"},
    "price_anomaly": {"upstream_shift", "pipeline_logic"},
    "slow_run": {"resource_or_slow"},
    "stale_data": {"freshness_or_schedule", "upstream_failure"},
    "normal": {"benign_change"},
}

_CATEGORY_PRIORITY = (
    "freshness_or_schedule",
    "replay_or_fanout",
    "upstream_shift",
    "resource_or_slow",
    "upstream_contract_change",
    "partial_load",
    "pipeline_logic",
    "upstream_failure",
    "benign_change",
    "unknown",
)

CATEGORY_KEYWORDS = {
    "upstream_failure": ("upstream", "extract", "timeout", "source failure", "api"),
    "upstream_contract_change": ("schema", "contract", "column", "renamed", "null"),
    "partial_load": ("partial", "load", "truncat", "row count", "filter", "missing rows"),
    "replay_or_fanout": ("duplicate", "replay", "idempot", "fan-out", "fanout"),
    "resource_or_slow": ("slow", "resource", "contention", "duration", "runtime", "latency"),
    "upstream_shift": ("distribution", "price", "shift", "pricing", "promotion"),
    "freshness_or_schedule": ("stale", "freshness", "schedule", "lag", "late", "delay"),
    "benign_change": ("benign", "expected", "normal", "seasonal"),
}


@dataclass(slots=True)
class EvaluationReport:
    provider: str
    incidents: int = 0
    completed: int = 0
    invalid: int = 0
    category_correct: int = 0
    category_correct_relaxed: int = 0
    grounded_statements: int = 0
    statements: int = 0
    refused: int = 0
    total_cost_usd: float = 0.0
    total_latency_ms: int = 0
    per_incident: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "incidents": self.incidents,
            "completed": self.completed,
            "invalid": self.invalid,
            "schema_valid_rate": round(self.completed / self.incidents, 4)
            if self.incidents
            else 0.0,
            "category_accuracy": round(self.category_correct / self.completed, 4)
            if self.completed
            else 0.0,
            "category_accuracy_relaxed": round(self.category_correct_relaxed / self.completed, 4)
            if self.completed
            else 0.0,
            "evidence_groundedness": round(self.grounded_statements / self.statements, 4)
            if self.statements
            else 0.0,
            "insufficient_evidence_rate": round(self.refused / self.completed, 4)
            if self.completed
            else 0.0,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "avg_latency_ms": round(self.total_latency_ms / self.completed, 1)
            if self.completed
            else 0.0,
            "per_incident": self.per_incident,
            "generated_at": utcnow().isoformat() + "Z",
        }


def evaluate_diagnoses(
    warehouse: Warehouse,
    settings: Settings,
    provider: DiagnosisProvider,
    *,
    limit: int = 24,
    force: bool = False,
) -> EvaluationReport:
    incidents = warehouse.query(
        f"""
        SELECT i.*, e.scenario_id
        FROM {settings.bq_detect_dataset}.incidents i
        LEFT JOIN {settings.bq_obs_dataset}.pipeline_executions e
          ON e.pipeline_id = i.primary_pipeline_id
         AND e.logical_date = CAST(i.bucket_start AS DATE)
        WHERE i.severity IN ('medium', 'high', 'critical')
        ORDER BY i.bucket_start DESC
        LIMIT ?
        """,
        (limit,),
    )
    report = EvaluationReport(provider=provider.name, incidents=len(incidents))

    for incident in incidents:
        incident_id = str(incident["incident_id"])
        outcome = diagnose_incident(warehouse, settings, incident_id, provider, force=force)
        row: dict[str, Any] = {
            "incident_id": incident_id,
            "date": _date_str(incident["bucket_start"]),
            "scenario": incident["scenario_id"],
            "expected_category": SCENARIO_CATEGORY.get(
                str(incident["scenario_id"] or "normal"), "unknown"
            ),
            "acceptable_categories": sorted(
                SCENARIO_ACCEPTED.get(
                    str(incident["scenario_id"] or "normal"),
                    {SCENARIO_CATEGORY.get(str(incident["scenario_id"] or "normal"), "unknown")},
                )
            ),
            "status": outcome.status,
            "cached": outcome.cached,
            "confidence": None,
            "predicted_category": None,
            "groundedness": None,
            "cost_usd": round(outcome.cost_usd, 6),
        }
        if outcome.payload is not None:
            report.completed += 1
            report.total_cost_usd += outcome.cost_usd
            report.total_latency_ms += outcome.latency_ms
            payload = outcome.payload
            predicted = _classify(payload.summary, payload.hypotheses)
            row["predicted_category"] = predicted
            row["confidence"] = payload.confidence
            row["insufficient_evidence"] = payload.insufficient_evidence
            if payload.insufficient_evidence:
                report.refused += 1
            if predicted == row["expected_category"]:
                report.category_correct += 1
            if predicted in row["acceptable_categories"]:
                report.category_correct_relaxed += 1

            stored = {
                item["evidence_id"]
                for item in warehouse.query(
                    f"SELECT evidence_id FROM {settings.bq_ai_dataset}.diagnosis_evidence "
                    "WHERE diagnosis_id = ?",
                    (outcome.diagnosis_id,),
                )
            }
            statements: list[Any] = [*payload.observed_facts, *payload.hypotheses]
            grounded = 0
            for statement in statements:
                if set(statement.evidence_ids) <= stored:
                    grounded += 1
            report.grounded_statements += grounded
            report.statements += len(statements)
            row["groundedness"] = round(grounded / len(statements), 4) if statements else None
        elif outcome.status in {"invalid", "failed"}:
            report.invalid += 1
        report.per_incident.append(row)

    report_dict = report.to_dict()
    report_dict["scenario_distribution"] = dict(
        Counter(str(item["scenario_id"]) for item in incidents)
    )
    target = settings.reports_dir / "diagnosis-eval.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report_dict, indent=2, sort_keys=True), encoding="utf-8")
    return report


def _classify(summary: str, hypotheses: list[Any]) -> str:
    text = str(hypotheses[0].cause).lower() if hypotheses else summary.lower()
    scores = {
        category: sum(1 for keyword in keywords if keyword in text)
        for category, keywords in CATEGORY_KEYWORDS.items()
    }
    ranked = sorted(
        scores,
        key=lambda category: (-scores[category], _CATEGORY_PRIORITY.index(category)),
    )
    best = ranked[0]
    return best if scores[best] > 0 else "unknown"


def _date_str(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


__all__ = [
    "CATEGORY_KEYWORDS",
    "SCENARIO_CATEGORY",
    "EvaluationReport",
    "evaluate_diagnoses",
]
