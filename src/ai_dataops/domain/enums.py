"""Domain vocabulary shared by the engines.

These enumerations mirror ``docs/data-model.md``. Entities and aggregates are
introduced with the engines that own them (R1 onward); only the controlled
vocabularies live here in R0.
"""

from __future__ import annotations

from enum import StrEnum


class ExecutionStatus(StrEnum):
    """Terminal and in-flight states of a pipeline execution."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class Severity(StrEnum):
    """Ordered severity used by rules, anomalies and incidents."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class QualityDimension(StrEnum):
    """The seven data quality dimensions monitored by the platform."""

    COMPLETENESS = "completeness"
    UNIQUENESS = "uniqueness"
    VALIDITY = "validity"
    CONSISTENCY = "consistency"
    INTEGRITY = "integrity"
    FRESHNESS = "freshness"
    SCHEMA = "schema"


class SignalType(StrEnum):
    """Origin of a signal that can be linked to an incident."""

    OPERATIONAL_FAILURE = "operational_failure"
    RULE_VIOLATION = "rule_violation"
    SCHEMA_CHANGE = "schema_change"
    STATISTICAL_ANOMALY = "statistical_anomaly"


class IncidentStatus(StrEnum):
    """Investigation lifecycle of an incident."""

    OPEN = "open"
    INVESTIGATING = "investigating"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"


__all__ = [
    "ExecutionStatus",
    "IncidentStatus",
    "QualityDimension",
    "Severity",
    "SignalType",
]
