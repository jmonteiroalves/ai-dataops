"""Enum vocabularies are a contract with the data model; keep them explicit."""

from __future__ import annotations

from ai_dataops.domain import (
    ExecutionStatus,
    IncidentStatus,
    QualityDimension,
    Severity,
    SignalType,
)


def test_execution_status_values() -> None:
    assert [status.value for status in ExecutionStatus] == [
        "running",
        "succeeded",
        "failed",
        "skipped",
    ]


def test_severity_is_ordered_from_info_to_critical() -> None:
    assert [level.value for level in Severity] == [
        "info",
        "low",
        "medium",
        "high",
        "critical",
    ]


def test_quality_dimensions_match_the_documented_seven() -> None:
    assert [dimension.value for dimension in QualityDimension] == [
        "completeness",
        "uniqueness",
        "validity",
        "consistency",
        "integrity",
        "freshness",
        "schema",
    ]


def test_signal_and_incident_vocabularies() -> None:
    assert SignalType.STATISTICAL_ANOMALY == "statistical_anomaly"
    assert SignalType.OPERATIONAL_FAILURE == "operational_failure"
    assert IncidentStatus.INVESTIGATING == "investigating"
    assert IncidentStatus.RESOLVED == "resolved"
