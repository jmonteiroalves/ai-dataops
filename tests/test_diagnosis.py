"""Diagnosis contract, evidence, validation, engine and evaluation (fake provider)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from ai_dataops.adapters.duckdb import DuckDBWarehouse
from ai_dataops.config import Settings
from ai_dataops.diagnosis.engine import diagnose_incident
from ai_dataops.diagnosis.evaluation import evaluate_diagnoses
from ai_dataops.diagnosis.evidence import build_evidence
from ai_dataops.diagnosis.provider import (
    DiagnosisProvider,
    DiagnosisRequest,
    DiagnosisResponse,
    FakeDiagnosisProvider,
)
from ai_dataops.diagnosis.schema import DiagnosisPayload
from ai_dataops.diagnosis.validation import parse_payload, validate_payload
from ai_dataops.errors import DiagnosisValidationError
from ai_dataops.ids import deterministic_id
from ai_dataops.instrumentation.recorder import ExecutionRecorder

DAY = date(2026, 11, 6)


def _seed_incident(
    warehouse: DuckDBWarehouse,
    settings: Settings,
    *,
    day: date = DAY,
    scenario: str = "volume_drop",
    metric: str = "records_processed",
    severity: str = "high",
) -> str:
    with ExecutionRecorder(
        warehouse,
        "sales_daily",
        day,
        obs_dataset=settings.bq_obs_dataset,
        scenario_id=scenario,
        metadata={"data_date": day.isoformat()},
    ) as recorder:
        recorder.set_counters(read=1000, processed=900, rejected=100)
        recorder.record_metric("pipeline", "sales_daily", metric, 900.0, "count")
    execution = warehouse.query(
        f"SELECT * FROM {settings.bq_obs_dataset}.pipeline_executions WHERE logical_date = ?",
        (day,),
    )[0]
    execution_id = str(execution["execution_id"])
    anomaly_id = deterministic_id("test-anomaly", execution_id, metric)
    warehouse.write_rows(
        f"{settings.bq_detect_dataset}.anomalies",
        [
            {
                "anomaly_id": anomaly_id,
                "entity_type": "pipeline",
                "entity_id": "sales_daily",
                "metric_name": metric,
                "execution_id": execution_id,
                "detected_at": datetime.combine(day, datetime.min.time()),
                "window_start": datetime.combine(day, datetime.min.time()),
                "window_end": datetime.combine(day, datetime.min.time()),
                "observed_value": 900.0,
                "expected_value": 1500.0,
                "lower_bound": 1300.0,
                "upper_bound": 1700.0,
                "deviation_abs": -600.0,
                "deviation_pct": -0.4,
                "score": -5.2,
                "method": "robust_z_weekday",
                "method_version": "1.0",
                "sample_size": 8,
                "severity": severity,
                "confidence": 0.9,
                "signal_type": "statistical_anomaly",
                "status": "open",
                "explanation": {},
                "created_at": datetime.combine(day, datetime.min.time()),
            }
        ],
    )
    incident_id = deterministic_id("test-incident", execution_id)
    warehouse.write_rows(
        f"{settings.bq_detect_dataset}.incidents",
        [
            {
                "incident_id": incident_id,
                "signature": "sig",
                "bucket_start": datetime.combine(day, datetime.min.time()),
                "title": "sales_daily: metric anomalies",
                "primary_pipeline_id": "sales_daily",
                "primary_dataset_key": f"{settings.demo_dataset}.sales_clean",
                "severity": severity,
                "status": "open",
                "opened_at": datetime.combine(day, datetime.min.time()),
                "closed_at": None,
                "evidence_count": 1,
                "has_diagnosis": False,
                "correlation_method": "heuristic_v1",
                "hypotheses_json": [{"cause": "Partial upstream load", "confidence": 0.6}],
                "created_at": datetime.combine(day, datetime.min.time()),
                "updated_at": datetime.combine(day, datetime.min.time()),
            }
        ],
    )
    warehouse.write_rows(
        f"{settings.bq_detect_dataset}.incident_events",
        [
            {
                "incident_id": incident_id,
                "event_type": "statistical_anomaly",
                "event_ref_id": anomaly_id,
                "linked_at": datetime.combine(day, datetime.min.time()),
                "weight": 0.9,
                "evidence": {},
            }
        ],
    )
    return incident_id


class CountingProvider:
    name = "counting"

    def __init__(self) -> None:
        self.calls = 0
        self._inner = FakeDiagnosisProvider()

    def generate(self, request: DiagnosisRequest) -> DiagnosisResponse:
        self.calls += 1
        return self._inner.generate(request)


def test_prompt_schema_matches_the_pydantic_contract() -> None:
    schema_path = Path(__file__).resolve().parents[1] / "prompts/incident_diagnosis/v3/schema.json"
    documented = json.loads(schema_path.read_text(encoding="utf-8"))
    contract = DiagnosisPayload.model_json_schema()

    assert set(documented["required"]) == set(contract["required"])
    assert set(documented["properties"]) == set(contract["properties"])
    assert (
        "requires_human_approval"
        not in documented["properties"]["suggested_actions"]["items"]["properties"]
    )


def test_evidence_bundle_excludes_the_scenario_label(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    incident_id = _seed_incident(warehouse, demo_settings)

    evidence = build_evidence(warehouse, demo_settings, incident_id)

    assert "volume_drop" not in evidence.canonical_json
    assert evidence.evidence_ids
    assert evidence.bundle["signals"]
    assert evidence.bundle["executions"]
    assert evidence.bundle["constraints"]["never_execute"] is True
    assert evidence.estimated_tokens > 0
    assert evidence.evidence_hash


def test_validation_rejects_unknown_evidence() -> None:
    provider = FakeDiagnosisProvider("unknown_evidence")
    request = DiagnosisRequest(system="s", user="EV-01", schema={}, prompt_id="p", prompt_version=1)
    response = provider.generate(request)
    payload = parse_payload(response.text)

    with pytest.raises(DiagnosisValidationError):
        validate_payload(payload, {"EV-01"})


def test_validation_rejects_causal_certainty() -> None:
    payload = DiagnosisPayload(
        summary="The root cause is definitely an upstream problem in this pipeline today.",
        observed_facts=[{"statement": "Volume dropped sharply.", "evidence_ids": ["EV-01"]}],
        affected_indicators=[],
        hypotheses=[
            {
                "cause": "Root cause is an upstream extract failure",
                "evidence_ids": ["EV-01"],
                "confidence": 0.8,
                "verification_steps": ["Check logs"],
            }
        ],
        impact={"scope": "revenue", "affected_consumers": [], "statement": "understated"},
        investigation_steps=["Check logs"],
        suggested_actions=[],
        confidence=0.8,
        limitations=["only this evidence"],
    )

    with pytest.raises(DiagnosisValidationError):
        validate_payload(payload, {"EV-01"})


def test_validation_rejects_overconfident_refusal() -> None:
    provider = FakeDiagnosisProvider("overconfident_refusal")
    request = DiagnosisRequest(system="s", user="EV-01", schema={}, prompt_id="p", prompt_version=1)
    payload = parse_payload(provider.generate(request).text)

    with pytest.raises(DiagnosisValidationError):
        validate_payload(payload, {"EV-01"})


def test_diagnosis_success_persists_contract_and_evidence(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    incident_id = _seed_incident(warehouse, demo_settings)

    outcome = diagnose_incident(warehouse, demo_settings, incident_id, FakeDiagnosisProvider())

    assert outcome.status == "success"
    assert outcome.payload is not None
    assert outcome.diagnosis_id
    diagnosis = warehouse.query(
        f"SELECT * FROM {demo_settings.bq_ai_dataset}.diagnoses WHERE diagnosis_id = ?",
        (outcome.diagnosis_id,),
    )[0]
    assert diagnosis["status"] == "success"
    assert diagnosis["is_current"] is True
    assert diagnosis["confidence"] == pytest.approx(0.7)
    assert diagnosis["prompt_version"] == 3

    evidence_rows = warehouse.query(
        f"SELECT COUNT(*) AS n FROM {demo_settings.bq_ai_dataset}.diagnosis_evidence "
        "WHERE diagnosis_id = ?",
        (outcome.diagnosis_id,),
    )[0]["n"]
    assert evidence_rows > 0
    recommendations = warehouse.query(
        f"SELECT COUNT(*) AS n FROM {demo_settings.bq_ai_dataset}.recommendations "
        "WHERE diagnosis_id = ?",
        (outcome.diagnosis_id,),
    )[0]["n"]
    assert recommendations == 1
    incident = warehouse.query(
        f"SELECT has_diagnosis FROM {demo_settings.bq_detect_dataset}.incidents "
        "WHERE incident_id = ?",
        (incident_id,),
    )[0]
    assert incident["has_diagnosis"] is True


def test_second_call_is_served_from_the_cache(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    incident_id = _seed_incident(warehouse, demo_settings)
    provider = CountingProvider()

    first = diagnose_incident(warehouse, demo_settings, incident_id, provider)
    second = diagnose_incident(warehouse, demo_settings, incident_id, provider)

    assert first.status == "success"
    assert second.status == "cached"
    assert second.cached is True
    assert provider.calls == 1


def test_budget_exhaustion_skips_the_call(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    incident_id = _seed_incident(warehouse, demo_settings)
    settings = demo_settings.model_copy(update={"diagnosis_budget_usd": 0.0})

    outcome = diagnose_incident(warehouse, settings, incident_id, FakeDiagnosisProvider())

    assert outcome.status == "skipped_budget"
    row = warehouse.query(
        f"SELECT status FROM {demo_settings.bq_ai_dataset}.diagnoses "
        "WHERE incident_id = ? ORDER BY requested_at DESC LIMIT 1",
        (incident_id,),
    )[0]
    assert row["status"] == "skipped_budget"


def test_invalid_response_is_recorded_not_hidden(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    incident_id = _seed_incident(warehouse, demo_settings)

    outcome = diagnose_incident(
        warehouse, demo_settings, incident_id, FakeDiagnosisProvider("invalid_json")
    )

    assert outcome.status == "invalid"
    row = warehouse.query(
        f"SELECT status, raw_response FROM {demo_settings.bq_ai_dataset}.diagnoses "
        "WHERE incident_id = ? ORDER BY requested_at DESC LIMIT 1",
        (incident_id,),
    )[0]
    assert row["status"] == "invalid"
    assert row["raw_response"]


def test_provider_failure_is_isolated(warehouse: DuckDBWarehouse, demo_settings: Settings) -> None:
    incident_id = _seed_incident(warehouse, demo_settings)

    outcome = diagnose_incident(
        warehouse, demo_settings, incident_id, FakeDiagnosisProvider("error")
    )

    assert outcome.status == "failed"
    assert outcome.error
    row = warehouse.query(
        f"SELECT status FROM {demo_settings.bq_ai_dataset}.diagnoses "
        "WHERE incident_id = ? ORDER BY requested_at DESC LIMIT 1",
        (incident_id,),
    )[0]
    assert row["status"] == "failed"


def test_evaluation_reports_groundedness_and_accuracy(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    _seed_incident(warehouse, demo_settings, day=DAY, scenario="volume_drop")
    _seed_incident(
        warehouse,
        demo_settings,
        day=DAY.replace(day=5),
        scenario="duplicate_spike",
        metric="duplicate_rate",
    )

    report = evaluate_diagnoses(warehouse, demo_settings, FakeDiagnosisProvider(), limit=10)

    payload = report.to_dict()
    assert payload["incidents"] == 2
    assert payload["completed"] == 2
    assert payload["schema_valid_rate"] == 1.0
    assert payload["evidence_groundedness"] == 1.0
    assert payload["category_accuracy"] == 0.5
    assert payload["total_cost_usd"] > 0
    assert (demo_settings.reports_dir / "diagnosis-eval.json").exists()


def test_evidence_rejects_unknown_incident(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    from ai_dataops.errors import NotFoundError

    with pytest.raises(NotFoundError):
        build_evidence(warehouse, demo_settings, "does-not-exist")


def test_provider_protocol_shape() -> None:
    provider: DiagnosisProvider = FakeDiagnosisProvider()
    request = DiagnosisRequest(system="s", user="EV-01", schema={}, prompt_id="p", prompt_version=1)

    response = provider.generate(request)

    assert response.model_id == "fake-model"
    assert json.loads(response.text)["summary"]
    assert isinstance(response.input_tokens, int)
