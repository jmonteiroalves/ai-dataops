"""Internal value objects shared by the engines.

These are plain dataclasses rather than persistence models: the warehouse stores
rows, and the engines exchange typed objects at their boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ai_dataops.domain.enums import QualityDimension, Severity, SignalType


@dataclass(frozen=True, slots=True)
class RuleDefinition:
    """An immutable, versioned quality rule as executed by the engine."""

    rule_id: str
    name: str
    dataset_key: str
    dimension: QualityDimension
    rule_type: str
    columns: tuple[str, ...]
    config: dict[str, Any]
    threshold: float
    warn_threshold: float | None
    weight: float
    severity: Severity
    owner: str | None
    description: str | None
    is_active: bool
    version: int
    config_hash: str


@dataclass(slots=True)
class RuleOutcome:
    """Result of evaluating one rule against one execution."""

    rule_id: str
    dataset_key: str
    dimension: QualityDimension
    severity: Severity
    status: str
    records_evaluated: int
    records_passed: int
    records_failed: int
    observed_value: float
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def pass_rate(self) -> float:
        if self.records_evaluated <= 0:
            return 0.0
        return self.records_passed / self.records_evaluated


@dataclass(slots=True)
class AnomalyRecord:
    """A detected deviation, ready to be persisted."""

    entity_type: str
    entity_id: str
    metric_name: str
    execution_id: str | None
    detected_at: datetime
    window_start: datetime
    window_end: datetime
    observed_value: float
    expected_value: float
    lower_bound: float
    upper_bound: float
    score: float
    method: str
    sample_size: int
    severity: Severity
    confidence: float
    signal_type: SignalType
    explanation: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Signal:
    """A correlation input: something that happened and may share a cause."""

    signal_type: SignalType
    ref_id: str
    entity_id: str
    dataset_key: str | None
    severity: Severity
    occurred_at: datetime
    summary: str


__all__ = ["AnomalyRecord", "RuleDefinition", "RuleOutcome", "Signal"]
