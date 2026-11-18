"""Post-generation validation.

Provider-side schema enforcement is an optimization; this module is the gate.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from ai_dataops.diagnosis.schema import DiagnosisPayload
from ai_dataops.errors import DiagnosisValidationError

FORBIDDEN_CAUSAL_PHRASES = (
    "root cause is",
    "caused by",
    "definitely caused",
    "proves that",
    "is definitely",
)


def parse_payload(raw: str) -> DiagnosisPayload:
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DiagnosisValidationError("response is not valid JSON") from exc
    if not isinstance(loaded, dict):
        raise DiagnosisValidationError("response must be a JSON object")
    try:
        return DiagnosisPayload.model_validate(loaded)
    except PydanticValidationError as exc:
        first = exc.errors(include_url=False)[0]
        location = ".".join(str(part) for part in first["loc"]) or "__root__"
        raise DiagnosisValidationError(f"schema violation at {location}: {first['msg']}") from exc


def validate_payload(payload: DiagnosisPayload, evidence_ids: set[str]) -> DiagnosisPayload:
    cited = payload.cited_evidence_ids()
    unknown = sorted(cited - evidence_ids)
    if unknown:
        raise DiagnosisValidationError(
            "response cites evidence that is not in the bundle",
            context={"unknown_evidence_ids": unknown},
        )

    text = " ".join(
        [payload.summary, *(hypothesis.cause for hypothesis in payload.hypotheses)]
    ).lower()
    hits = [phrase for phrase in FORBIDDEN_CAUSAL_PHRASES if phrase in text]
    if hits:
        raise DiagnosisValidationError("response asserts causality", context={"phrases": hits})

    if payload.insufficient_evidence and payload.confidence > 0.4:
        raise DiagnosisValidationError(
            "insufficient evidence must not be reported with high confidence"
        )
    if not payload.insufficient_evidence and not payload.hypotheses:
        raise DiagnosisValidationError("a diagnosis with evidence must include a hypothesis")
    return payload


def schema_snapshot() -> dict[str, Any]:
    """JSON schema of the contract, used by documentation tests."""
    return DiagnosisPayload.model_json_schema()


__all__ = ["FORBIDDEN_CAUSAL_PHRASES", "parse_payload", "schema_snapshot", "validate_payload"]
