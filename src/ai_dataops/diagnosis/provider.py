"""Model provider abstraction.

Vertex AI is the production path. The fake provider is deterministic and is what CI uses,
so the test suite never spends money or depends on the network.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from ai_dataops.errors import ProviderError

FakeMode = Literal[
    "valid",
    "invalid_json",
    "unknown_evidence",
    "refusal",
    "overconfident_refusal",
    "error",
    "prose",
]


@dataclass(frozen=True, slots=True)
class DiagnosisRequest:
    system: str
    user: str
    schema: dict[str, Any]
    prompt_id: str
    prompt_version: int


@dataclass(frozen=True, slots=True)
class DiagnosisResponse:
    text: str
    model_id: str
    model_version: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class DiagnosisProvider(Protocol):
    name: str

    def generate(self, request: DiagnosisRequest) -> DiagnosisResponse: ...


class FakeDiagnosisProvider:
    """Deterministic provider for tests and offline demos."""

    name = "fake"

    def __init__(self, mode: FakeMode = "valid") -> None:
        self.mode = mode

    def generate(self, request: DiagnosisRequest) -> DiagnosisResponse:
        if self.mode == "error":
            raise ProviderError("simulated provider outage")
        if self.mode == "prose":
            return self._response("I could not produce JSON for this incident.")
        if self.mode == "invalid_json":
            return self._response('{"summary": "truncated"')

        ids = sorted(set(re.findall(r"EV-\d+", request.user))) or ["EV-01"]
        if self.mode == "unknown_evidence":
            ids = ["EV-999"]
        insufficient = self.mode in {"refusal", "overconfident_refusal"}
        confidence = 0.9 if self.mode == "overconfident_refusal" else (0.3 if insufficient else 0.7)
        payload = {
            "summary": (
                "The incident shows a deviation in the monitored indicators; the evidence "
                "supports investigation of the upstream load before any remediation."
            ),
            "observed_facts": [
                {
                    "statement": "The affected indicators deviated from their expected baseline.",
                    "evidence_ids": ids[:2],
                }
            ],
            "affected_indicators": [
                {
                    "name": "records_processed",
                    "observed": 925.0,
                    "expected": 1652.0,
                    "unit": "count",
                }
            ],
            "hypotheses": []
            if insufficient
            else [
                {
                    "cause": "Partial upstream load consistent with a truncated extract",
                    "evidence_ids": ids[:2],
                    "confidence": 0.55,
                    "verification_steps": ["Compare the source manifest with the landing file"],
                }
            ],
            "impact": {
                "scope": "daily revenue marts",
                "affected_consumers": ["sales_dashboard"],
                "statement": "Revenue for the day is likely understated.",
            },
            "investigation_steps": ["Check the extract logs for the affected window"],
            "suggested_actions": [
                {
                    "action": "Re-run the extract after confirming the source row count",
                    "priority": "high",
                }
            ],
            "confidence": confidence,
            "limitations": ["Only the provided evidence was considered."],
            "insufficient_evidence": insufficient,
        }
        return self._response(json.dumps(payload))

    @staticmethod
    def _response(text: str) -> DiagnosisResponse:
        return DiagnosisResponse(
            text=text,
            model_id="fake-model",
            model_version="fake-1",
            input_tokens=max(1, len(text) // 4),
            output_tokens=max(1, len(text) // 8),
            latency_ms=5,
        )


__all__ = [
    "DiagnosisProvider",
    "DiagnosisRequest",
    "DiagnosisResponse",
    "FakeDiagnosisProvider",
]
