"""Pydantic contract for diagnoses.

The model is the authority: prompts/v1/schema.json is a documentation snapshot and a test
asserts that it stays compatible.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Indicator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    observed: float
    expected: float | None = None
    unit: str


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=10, max_length=400)
    evidence_ids: list[str] = Field(min_length=1)


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cause: str = Field(min_length=5, max_length=300)
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    verification_steps: list[str] = Field(min_length=1, max_length=5)


class Impact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str
    affected_consumers: list[str] = Field(default_factory=list)
    statement: str


class SuggestedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    priority: Literal["low", "medium", "high"]


class DiagnosisPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=40, max_length=600)
    observed_facts: list[Fact] = Field(min_length=1, max_length=8)
    affected_indicators: list[Indicator] = Field(default_factory=list, max_length=8)
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=5)
    impact: Impact
    investigation_steps: list[str] = Field(min_length=1, max_length=6)
    suggested_actions: list[SuggestedAction] = Field(default_factory=list, max_length=5)
    confidence: float = Field(ge=0.0, le=1.0)
    limitations: list[str] = Field(min_length=1, max_length=5)
    insufficient_evidence: bool = False

    def cited_evidence_ids(self) -> set[str]:
        cited: set[str] = set()
        for fact in self.observed_facts:
            cited.update(fact.evidence_ids)
        for hypothesis in self.hypotheses:
            cited.update(hypothesis.evidence_ids)
        return cited


__all__ = [
    "DiagnosisPayload",
    "Fact",
    "Hypothesis",
    "Impact",
    "Indicator",
    "SuggestedAction",
]
