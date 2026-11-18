"""Diagnosis orchestration: gate, deduplicate, call, validate, persist."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from time import perf_counter
from typing import Any

from ai_dataops.config import Settings
from ai_dataops.diagnosis.evidence import EvidenceBundle, build_evidence
from ai_dataops.diagnosis.prompt import (
    PromptTemplate,
    ensure_model,
    ensure_prompt_version,
    load_prompt,
)
from ai_dataops.diagnosis.provider import DiagnosisProvider, DiagnosisRequest
from ai_dataops.diagnosis.schema import DiagnosisPayload
from ai_dataops.diagnosis.validation import parse_payload, validate_payload
from ai_dataops.domain.enums import Severity
from ai_dataops.domain.ports import Warehouse
from ai_dataops.errors import (
    DiagnosisValidationError,
    NotFoundError,
    ProviderError,
)
from ai_dataops.ids import deterministic_id
from ai_dataops.logging import get_logger
from ai_dataops.timeutil import utcnow

_SEVERITY_ORDER = (
    Severity.INFO,
    Severity.LOW,
    Severity.MEDIUM,
    Severity.HIGH,
    Severity.CRITICAL,
)

_DEFAULT_PRICES = (0.30, 2.50)

logger = get_logger("diagnosis")


@dataclass(slots=True)
class DiagnosisOutcome:
    status: str
    diagnosis_id: str | None = None
    cached: bool = False
    payload: DiagnosisPayload | None = None
    cost_usd: float = 0.0
    latency_ms: int = 0
    error: str | None = None


def diagnose_incident(
    warehouse: Warehouse,
    settings: Settings,
    incident_id: str,
    provider: DiagnosisProvider,
    *,
    force: bool = False,
) -> DiagnosisOutcome:
    if not settings.diagnosis_enabled:
        return DiagnosisOutcome(status="skipped_disabled")

    incident = _incident(warehouse, settings, incident_id)
    if _rank(str(incident["severity"])) < _rank(settings.diagnosis_min_severity):
        return DiagnosisOutcome(status="skipped_severity")

    prompt = ensure_prompt_version(warehouse, settings, load_prompt(settings))
    ensure_model(warehouse, settings, prompt)
    evidence = build_evidence(warehouse, settings, incident_id)

    cached = _current_diagnosis(warehouse, settings, incident_id, evidence, prompt)
    if cached is not None and not force:
        cached_payload = _payload_from_row(cached)
        return DiagnosisOutcome(
            status="cached",
            diagnosis_id=str(cached["diagnosis_id"]),
            cached=True,
            payload=cached_payload,
            cost_usd=float(cached["estimated_cost_usd"] or 0.0),
            latency_ms=int(cached["latency_ms"] or 0),
        )

    spent = _month_spend(warehouse, settings)
    if spent >= settings.diagnosis_budget_usd:
        _persist_skip(warehouse, settings, incident_id, prompt, evidence, "skipped_budget")
        return DiagnosisOutcome(status="skipped_budget")

    request = DiagnosisRequest(
        system=prompt.system,
        user=prompt.render(evidence.render()),
        schema=DiagnosisPayload.model_json_schema(),
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.version,
    )
    started = perf_counter()
    try:
        response = provider.generate(request)
    except ProviderError as exc:
        latency_ms = int((perf_counter() - started) * 1000)
        _persist_failure(
            warehouse,
            settings,
            incident_id,
            prompt,
            evidence,
            provider.name,
            status="failed",
            raw="",
            error=str(exc),
            latency_ms=latency_ms,
            cost=0.0,
        )
        return DiagnosisOutcome(status="failed", error=str(exc), latency_ms=latency_ms)

    latency_ms = response.latency_ms or int((perf_counter() - started) * 1000)
    cost = _cost(warehouse, settings, prompt, response.input_tokens, response.output_tokens)

    payload: DiagnosisPayload | None = None
    error: str | None = None
    try:
        payload = validate_payload(parse_payload(response.text), evidence.evidence_ids)
    except DiagnosisValidationError as first_error:
        logger.warning(
            "diagnosis failed validation, attempting one repair",
            extra={"incident_id": incident_id, "reason": str(first_error)},
        )
        repair = DiagnosisRequest(
            system=prompt.system,
            user=(
                f"{request.user}\n\nYour previous reply was invalid: {first_error}. "
                "Return only JSON that conforms to the schema and cites existing evidence ids."
            ),
            schema=request.schema,
            prompt_id=request.prompt_id,
            prompt_version=request.prompt_version,
        )
        try:
            repaired = provider.generate(repair)
            latency_ms += repaired.latency_ms
            cost += _cost(
                warehouse, settings, prompt, repaired.input_tokens, repaired.output_tokens
            )
            payload = validate_payload(parse_payload(repaired.text), evidence.evidence_ids)
            response = replace(
                repaired,
                input_tokens=response.input_tokens + repaired.input_tokens,
                output_tokens=response.output_tokens + repaired.output_tokens,
            )
        except (DiagnosisValidationError, ProviderError) as second_error:
            error = str(second_error)

    if payload is None:
        _persist_failure(
            warehouse,
            settings,
            incident_id,
            prompt,
            evidence,
            provider.name,
            status="invalid",
            raw=response.text,
            error=error or "validation failed",
            latency_ms=latency_ms,
            cost=cost,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
        return DiagnosisOutcome(status="invalid", error=error, cost_usd=cost, latency_ms=latency_ms)

    diagnosis_id = _persist_success(
        warehouse,
        settings,
        incident_id,
        prompt,
        evidence,
        provider.name,
        response,
        payload,
        latency_ms,
        cost,
    )
    return DiagnosisOutcome(
        status="success",
        diagnosis_id=diagnosis_id,
        payload=payload,
        cost_usd=cost,
        latency_ms=latency_ms,
    )


def pending_incidents(
    warehouse: Warehouse,
    settings: Settings,
    *,
    limit: int | None = None,
    logical_date: date | None = None,
) -> list[dict[str, Any]]:
    filters = ["i.severity IN ('medium', 'high', 'critical')"]
    params: list[Any] = []
    if logical_date:
        filters.append("CAST(i.bucket_start AS DATE) = ?")
        params.append(logical_date)
    filters.append(
        f"NOT EXISTS (SELECT 1 FROM {settings.bq_ai_dataset}.diagnoses d "
        "WHERE d.incident_id = i.incident_id AND d.is_current = TRUE)"
    )
    query = (
        f"SELECT i.* FROM {settings.bq_detect_dataset}.incidents i "
        f"WHERE {' AND '.join(filters)} ORDER BY i.bucket_start DESC LIMIT ?"
    )
    params.append(limit or settings.diagnosis_max_per_run)
    return warehouse.query(query, tuple(params))


def _incident(warehouse: Warehouse, settings: Settings, incident_id: str) -> dict[str, Any]:
    rows = warehouse.query(
        f"SELECT * FROM {settings.bq_detect_dataset}.incidents WHERE incident_id = ?",
        (incident_id,),
    )
    if not rows:
        raise NotFoundError("incident not found", context={"incident_id": incident_id})
    return rows[0]


def _rank(value: str) -> int:
    try:
        return _SEVERITY_ORDER.index(Severity(value))
    except ValueError:
        return 0


def _current_diagnosis(
    warehouse: Warehouse,
    settings: Settings,
    incident_id: str,
    evidence: EvidenceBundle,
    prompt: PromptTemplate,
) -> dict[str, Any] | None:
    rows = warehouse.query(
        f"""
        SELECT * FROM {settings.bq_ai_dataset}.diagnoses
        WHERE incident_id = ? AND evidence_hash = ? AND prompt_version = ?
          AND status = 'success' AND is_current = TRUE
        LIMIT 1
        """,
        (incident_id, evidence.evidence_hash, prompt.version),
    )
    return rows[0] if rows else None


def _payload_from_row(row: dict[str, Any]) -> DiagnosisPayload | None:
    try:
        return DiagnosisPayload.model_validate(
            {
                "summary": row["summary"],
                "observed_facts": json.loads(row["observed_facts"] or "[]"),
                "affected_indicators": json.loads(row["affected_indicators"] or "[]"),
                "hypotheses": json.loads(row["hypotheses"] or "[]"),
                "impact": json.loads(row["impact"] or "{}"),
                "investigation_steps": json.loads(row["investigation_steps"] or "[]"),
                "suggested_actions": json.loads(row["suggested_actions"] or "[]"),
                "confidence": row["confidence"] or 0.0,
                "limitations": json.loads(row["limitations"] or "[]"),
                "insufficient_evidence": bool(row["insufficient_evidence"]),
            }
        )
    except Exception:  # pragma: no cover - defensive
        return None


def _month_start() -> datetime:
    now = utcnow()
    return datetime(now.year, now.month, 1)


def _month_spend(warehouse: Warehouse, settings: Settings) -> float:
    rows = warehouse.query(
        f"SELECT COALESCE(SUM(estimated_cost_usd), 0) AS total "
        f"FROM {settings.bq_ai_dataset}.ai_requests WHERE created_at >= ?",
        (_month_start(),),
    )
    return float(rows[0]["total"] or 0.0)


def _prices(warehouse: Warehouse, settings: Settings, model_id: str) -> tuple[float, float]:
    rows = warehouse.query(
        f"SELECT input_price_per_mtok, output_price_per_mtok "
        f"FROM {settings.bq_meta_dataset}.llm_models WHERE model_id = ? "
        "AND valid_to IS NULL ORDER BY valid_from DESC LIMIT 1",
        (model_id,),
    )
    if not rows:
        return _DEFAULT_PRICES
    return (float(rows[0]["input_price_per_mtok"]), float(rows[0]["output_price_per_mtok"]))


def _cost(
    warehouse: Warehouse,
    settings: Settings,
    prompt: PromptTemplate,
    input_tokens: int,
    output_tokens: int,
) -> float:
    input_price, output_price = _prices(warehouse, settings, prompt.model_id)
    return round((input_tokens * input_price + output_tokens * output_price) / 1_000_000, 6)


def _persist_success(
    warehouse: Warehouse,
    settings: Settings,
    incident_id: str,
    prompt: PromptTemplate,
    evidence: EvidenceBundle,
    provider_name: str,
    response: Any,
    payload: DiagnosisPayload,
    latency_ms: int,
    cost: float,
) -> str:
    now = utcnow()
    diagnosis_id = deterministic_id(
        "diagnosis", incident_id, evidence.evidence_hash, prompt.version
    )
    warehouse.execute(
        f"UPDATE {settings.bq_ai_dataset}.diagnoses SET is_current = FALSE "
        "WHERE incident_id = ? AND is_current = TRUE",
        (incident_id,),
    )
    warehouse.write_rows(
        f"{settings.bq_ai_dataset}.diagnoses",
        [
            {
                "diagnosis_id": diagnosis_id,
                "incident_id": incident_id,
                "prompt_id": prompt.prompt_id,
                "prompt_version": prompt.version,
                "model_id": response.model_id,
                "model_version": response.model_version,
                "evidence_hash": evidence.evidence_hash,
                "requested_at": now,
                "responded_at": now,
                "latency_ms": latency_ms,
                "status": "success",
                "summary": payload.summary,
                "observed_facts": [fact.model_dump() for fact in payload.observed_facts],
                "affected_indicators": [
                    indicator.model_dump() for indicator in payload.affected_indicators
                ],
                "hypotheses": [hypothesis.model_dump() for hypothesis in payload.hypotheses],
                "impact": payload.impact.model_dump(),
                "investigation_steps": payload.investigation_steps,
                "suggested_actions": [action.model_dump() for action in payload.suggested_actions],
                "confidence": payload.confidence,
                "limitations": payload.limitations,
                "insufficient_evidence": payload.insufficient_evidence,
                "raw_response": response.text[:20000],
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "estimated_cost_usd": cost,
                "is_current": True,
                "superseded_by": None,
            }
        ],
    )
    warehouse.write_rows(
        f"{settings.bq_ai_dataset}.diagnosis_evidence",
        [
            {
                "diagnosis_id": diagnosis_id,
                "evidence_id": entry["evidence_id"],
                "evidence_type": entry["evidence_type"],
                "ref_id": entry["ref_id"],
                "payload_excerpt": entry["payload_excerpt"],
            }
            for entry in evidence.entries
        ],
    )
    if payload.suggested_actions:
        warehouse.write_rows(
            f"{settings.bq_ai_dataset}.recommendations",
            [
                {
                    "recommendation_id": deterministic_id("recommendation", diagnosis_id, index),
                    "diagnosis_id": diagnosis_id,
                    "incident_id": incident_id,
                    "category": "suggested_action",
                    "action": action.action,
                    "priority": action.priority,
                    "status": "proposed",
                    "owner": None,
                    "created_at": now,
                    "resolved_at": None,
                }
                for index, action in enumerate(payload.suggested_actions)
            ],
        )
    warehouse.write_rows(
        f"{settings.bq_ai_dataset}.ai_requests",
        [
            {
                "request_id": deterministic_id(
                    "ai_request", diagnosis_id, str(latency_ms), provider_name
                ),
                "incident_id": incident_id,
                "diagnosis_id": diagnosis_id,
                "outcome": "success",
                "latency_ms": latency_ms,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "estimated_cost_usd": cost,
                "error_code": None,
                "created_at": now,
            }
        ],
    )
    warehouse.execute(
        f"UPDATE {settings.bq_detect_dataset}.incidents SET has_diagnosis = TRUE, "
        "updated_at = ? WHERE incident_id = ?",
        (now, incident_id),
    )
    return diagnosis_id


def _persist_failure(
    warehouse: Warehouse,
    settings: Settings,
    incident_id: str,
    prompt: PromptTemplate,
    evidence: EvidenceBundle,
    provider_name: str,
    *,
    status: str,
    raw: str,
    error: str,
    latency_ms: int,
    cost: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> str:
    now = utcnow()
    diagnosis_id = deterministic_id(
        "diagnosis", incident_id, evidence.evidence_hash, prompt.version
    )
    warehouse.write_rows(
        f"{settings.bq_ai_dataset}.diagnoses",
        [
            {
                "diagnosis_id": diagnosis_id,
                "incident_id": incident_id,
                "prompt_id": prompt.prompt_id,
                "prompt_version": prompt.version,
                "model_id": prompt.model_id,
                "model_version": provider_name,
                "evidence_hash": evidence.evidence_hash,
                "requested_at": now,
                "responded_at": now,
                "latency_ms": latency_ms,
                "status": status,
                "summary": None,
                "observed_facts": [],
                "affected_indicators": [],
                "hypotheses": [],
                "impact": {},
                "investigation_steps": [],
                "suggested_actions": [],
                "confidence": None,
                "limitations": [error[:300]],
                "insufficient_evidence": False,
                "raw_response": raw[:20000],
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated_cost_usd": cost,
                "is_current": False,
                "superseded_by": None,
            }
        ],
    )
    warehouse.write_rows(
        f"{settings.bq_ai_dataset}.ai_requests",
        [
            {
                "request_id": deterministic_id("ai_request", diagnosis_id, status, str(latency_ms)),
                "incident_id": incident_id,
                "diagnosis_id": diagnosis_id,
                "outcome": status,
                "latency_ms": latency_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated_cost_usd": cost,
                "error_code": error[:200],
                "created_at": now,
            }
        ],
    )
    return diagnosis_id


def _persist_skip(
    warehouse: Warehouse,
    settings: Settings,
    incident_id: str,
    prompt: PromptTemplate,
    evidence: EvidenceBundle,
    status: str,
) -> None:
    diagnosis_id = deterministic_id(
        "diagnosis", incident_id, evidence.evidence_hash, prompt.version
    )
    now = utcnow()
    warehouse.write_rows(
        f"{settings.bq_ai_dataset}.diagnoses",
        [
            {
                "diagnosis_id": diagnosis_id,
                "incident_id": incident_id,
                "prompt_id": prompt.prompt_id,
                "prompt_version": prompt.version,
                "model_id": prompt.model_id,
                "model_version": "not_called",
                "evidence_hash": evidence.evidence_hash,
                "requested_at": now,
                "responded_at": None,
                "latency_ms": 0,
                "status": status,
                "summary": None,
                "observed_facts": [],
                "affected_indicators": [],
                "hypotheses": [],
                "impact": {},
                "investigation_steps": [],
                "suggested_actions": [],
                "confidence": None,
                "limitations": ["monthly diagnosis budget exhausted"],
                "insufficient_evidence": False,
                "raw_response": None,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
                "is_current": False,
                "superseded_by": None,
            }
        ],
    )
    warehouse.write_rows(
        f"{settings.bq_ai_dataset}.ai_requests",
        [
            {
                "request_id": deterministic_id("ai_request", diagnosis_id, status),
                "incident_id": incident_id,
                "diagnosis_id": diagnosis_id,
                "outcome": status,
                "latency_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
                "error_code": None,
                "created_at": now,
            }
        ],
    )


__all__ = ["DiagnosisOutcome", "diagnose_incident", "pending_incidents"]
