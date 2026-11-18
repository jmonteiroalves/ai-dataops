"""Quality engine: evaluate rules, persist results and aggregate scores."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from time import perf_counter
from typing import Any

from ai_dataops.config import Settings
from ai_dataops.domain.models import RuleDefinition, RuleOutcome
from ai_dataops.domain.ports import Warehouse
from ai_dataops.errors import DataOpsError
from ai_dataops.ids import deterministic_id
from ai_dataops.quality.compiler import (
    compile_freshness,
    compile_row_rule,
    compile_samples,
)
from ai_dataops.timeutil import utcnow

VALID_STATUSES = frozenset({"pass", "warn", "fail", "error", "skipped", "insufficient_data"})


def run_quality(
    warehouse: Warehouse,
    settings: Settings,
    execution: Mapping[str, Any],
    rules: Sequence[RuleDefinition],
) -> list[RuleOutcome]:
    metadata = _metadata(execution)
    data_date = date.fromisoformat(str(metadata["data_date"]))
    logical_date = _logical_date(execution)
    outcomes: list[RuleOutcome] = []

    for rule in rules:
        if not rule.is_active:
            continue
        started = perf_counter()
        try:
            outcome = _evaluate(warehouse, settings, rule, execution, data_date, logical_date)
        except DataOpsError as exc:
            outcome = _error_outcome(rule, str(exc))
        except Exception as exc:  # pragma: no cover - defensive isolation
            outcome = _error_outcome(rule, f"{type(exc).__name__}: {exc}")
        duration_ms = int((perf_counter() - started) * 1000)
        _persist_result(warehouse, settings, execution, rule, outcome, duration_ms)
        outcomes.append(outcome)

    _persist_scores(warehouse, settings, execution, rules, outcomes)
    return outcomes


def _logical_date(execution: Mapping[str, Any]) -> date:
    value = execution["logical_date"]
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _regex_function(settings: Settings) -> str:
    return "REGEXP_CONTAINS" if settings.engine.value == "bigquery" else "REGEXP_MATCHES"


def _metadata(execution: Mapping[str, Any]) -> dict[str, Any]:
    raw = execution.get("execution_metadata") or "{}"
    if isinstance(raw, str):
        loaded: dict[str, Any] = json.loads(raw)
        return loaded
    return dict(raw)


def _evaluate(
    warehouse: Warehouse,
    settings: Settings,
    rule: RuleDefinition,
    execution: Mapping[str, Any],
    data_date: date,
    logical_date: date,
) -> RuleOutcome:
    if rule.rule_type == "schema_contract":
        return _evaluate_schema(warehouse, settings, rule, execution)
    if rule.rule_type == "freshness":
        return _evaluate_freshness(warehouse, rule, logical_date, str(execution["execution_id"]))

    compiled = compile_row_rule(
        rule,
        rule.dataset_key,
        data_date,
        regex_function=_regex_function(settings),
    )
    rows = warehouse.query(compiled.sql, compiled.params)
    row = rows[0] if rows else {}
    evaluated = int(row.get("records_evaluated") or 0)
    if evaluated == 0:
        return RuleOutcome(
            rule_id=rule.rule_id,
            dataset_key=rule.dataset_key,
            dimension=rule.dimension,
            severity=rule.severity,
            status="insufficient_data",
            records_evaluated=0,
            records_passed=0,
            records_failed=0,
            observed_value=0.0,
            details={"reason": "no rows for the execution data date"},
        )

    passed = int(row.get("records_passed") or 0)
    failed = evaluated - passed
    observed = failed / evaluated
    details: dict[str, Any] = {"sample_size": evaluated, "failure_rate": round(observed, 6)}
    status = _status_for(rule, observed)
    if status in {"fail", "warn"}:
        samples = compile_samples(
            rule,
            rule.dataset_key,
            data_date,
            regex_function=_regex_function(settings),
        )
        if samples is not None:
            details["samples"] = [
                item["sample"] for item in warehouse.query(samples.sql, samples.params)
            ]

    return RuleOutcome(
        rule_id=rule.rule_id,
        dataset_key=rule.dataset_key,
        dimension=rule.dimension,
        severity=rule.severity,
        status=status,
        records_evaluated=evaluated,
        records_passed=passed,
        records_failed=failed,
        observed_value=round(observed, 6),
        details=details,
    )


def _evaluate_freshness(
    warehouse: Warehouse,
    rule: RuleDefinition,
    logical_date: date,
    execution_id: str,
) -> RuleOutcome:
    compiled = compile_freshness(rule, rule.dataset_key, execution_id)
    rows = warehouse.query(compiled.sql, compiled.params)
    row = rows[0] if rows else {}
    max_date = row.get("max_date")
    if max_date is None:
        return RuleOutcome(
            rule_id=rule.rule_id,
            dataset_key=rule.dataset_key,
            dimension=rule.dimension,
            severity=rule.severity,
            status="insufficient_data",
            records_evaluated=0,
            records_passed=0,
            records_failed=0,
            observed_value=0.0,
            details={"reason": "dataset has no rows"},
        )
    if isinstance(max_date, datetime):
        max_date = max_date.date()
    lag_minutes = float((logical_date - max_date).days * 1440)
    sla = float(rule.config.get("sla_minutes", 1560))
    if lag_minutes <= sla:
        status = "pass"
    elif lag_minutes <= sla * 1.2:
        status = "warn"
    else:
        status = "fail"
    return RuleOutcome(
        rule_id=rule.rule_id,
        dataset_key=rule.dataset_key,
        dimension=rule.dimension,
        severity=rule.severity,
        status=status,
        records_evaluated=1,
        records_passed=1 if status == "pass" else 0,
        records_failed=0 if status == "pass" else 1,
        observed_value=lag_minutes,
        details={"sla_minutes": sla, "max_order_date": max_date.isoformat()},
    )


def _evaluate_schema(
    warehouse: Warehouse,
    settings: Settings,
    rule: RuleDefinition,
    execution: Mapping[str, Any],
) -> RuleOutcome:
    rows = warehouse.query(
        f"""
        SELECT column_name, ordinal_position
        FROM {settings.bq_obs_dataset}.schema_snapshots
        WHERE dataset_key = ? AND execution_id = ?
        ORDER BY ordinal_position
        """,
        (rule.dataset_key, execution["execution_id"]),
    )
    if not rows:
        return RuleOutcome(
            rule_id=rule.rule_id,
            dataset_key=rule.dataset_key,
            dimension=rule.dimension,
            severity=rule.severity,
            status="insufficient_data",
            records_evaluated=0,
            records_passed=0,
            records_failed=0,
            observed_value=0.0,
            details={"reason": "no schema snapshot for the execution"},
        )
    expected = [str(column) for column in rule.config.get("expected", [])]
    actual = [str(row["column_name"]) for row in rows]
    missing = [column for column in expected if column not in actual]
    unexpected = [column for column in actual if column not in expected]
    passed = len(expected) - len(missing)
    evaluated = len(expected)
    observed = passed / evaluated if evaluated else 0.0
    status = "pass" if not missing and not unexpected else "fail"
    return RuleOutcome(
        rule_id=rule.rule_id,
        dataset_key=rule.dataset_key,
        dimension=rule.dimension,
        severity=rule.severity,
        status=status,
        records_evaluated=evaluated,
        records_passed=passed,
        records_failed=len(missing),
        observed_value=round(observed, 6),
        details={
            "missing": missing,
            "unexpected": unexpected,
            "classification": "breaking" if missing else "additive",
            "actual_columns": actual,
        },
    )


def _status_for(rule: RuleDefinition, observed_bad_rate: float) -> str:
    conformity_rate = 1.0 - observed_bad_rate
    if conformity_rate >= rule.threshold:
        return "pass"
    if rule.warn_threshold is not None and conformity_rate >= rule.warn_threshold:
        return "warn"
    return "fail"


def _error_outcome(rule: RuleDefinition, message: str) -> RuleOutcome:
    return RuleOutcome(
        rule_id=rule.rule_id,
        dataset_key=rule.dataset_key,
        dimension=rule.dimension,
        severity=rule.severity,
        status="error",
        records_evaluated=0,
        records_passed=0,
        records_failed=0,
        observed_value=0.0,
        details={"error": message},
    )


def _persist_result(
    warehouse: Warehouse,
    settings: Settings,
    execution: Mapping[str, Any],
    rule: RuleDefinition,
    outcome: RuleOutcome,
    duration_ms: int,
) -> None:
    warehouse.write_rows(
        f"{settings.bq_quality_dataset}.quality_rule_results",
        [
            {
                "result_id": deterministic_id(
                    "result", execution["execution_id"], rule.rule_id, rule.version
                ),
                "execution_id": execution["execution_id"],
                "rule_id": rule.rule_id,
                "rule_version": rule.version,
                "dataset_key": rule.dataset_key,
                "evaluated_at": utcnow(),
                "status": outcome.status,
                "records_evaluated": outcome.records_evaluated,
                "records_passed": outcome.records_passed,
                "records_failed": outcome.records_failed,
                "pass_rate": round(outcome.pass_rate, 6),
                "observed_value": outcome.observed_value,
                "threshold": rule.threshold,
                "severity": rule.severity.value,
                "details": outcome.details,
                "duration_ms": duration_ms,
                "created_at": utcnow(),
            }
        ],
    )


def _persist_scores(
    warehouse: Warehouse,
    settings: Settings,
    execution: Mapping[str, Any],
    rules: Sequence[RuleDefinition],
    outcomes: Sequence[RuleOutcome],
) -> None:
    weights = {rule.rule_id: rule.weight for rule in rules}
    groups: dict[tuple[str, str], list[RuleOutcome]] = {}
    for outcome in outcomes:
        groups.setdefault((outcome.dataset_key, outcome.dimension.value), []).append(outcome)

    for (dataset_key, dimension), items in groups.items():
        valid = [item for item in items if item.status in {"pass", "warn", "fail"}]
        weight_total = sum(weights.get(item.rule_id, 1.0) for item in valid)
        score: float | None = None
        if weight_total > 0:
            score = (
                sum(weights.get(item.rule_id, 1.0) * item.pass_rate for item in valid)
                / weight_total
            )
        warehouse.write_rows(
            f"{settings.bq_quality_dataset}.quality_scores",
            [
                {
                    "score_id": deterministic_id(
                        "score", execution["execution_id"], dataset_key, dimension
                    ),
                    "execution_id": execution["execution_id"],
                    "dataset_key": dataset_key,
                    "dimension": dimension,
                    "rules_total": len(items),
                    "rules_passed": sum(1 for item in items if item.status == "pass"),
                    "rules_failed": sum(1 for item in items if item.status == "fail"),
                    "rules_error": sum(1 for item in items if item.status == "error"),
                    "score": round(score, 6) if score is not None else None,
                    "weight_total": round(weight_total, 6),
                    "created_at": utcnow(),
                }
            ],
        )


__all__ = ["VALID_STATUSES", "run_quality"]
