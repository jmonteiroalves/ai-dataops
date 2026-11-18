"""Compile declarative rules into SQL.

Only the DuckDB dialect is implemented in R1. Dialect-specific functions
(``REGEXP_MATCHES``) are isolated in ``_condition`` so a BigQuery compiler can be
added without touching the engine (R3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from ai_dataops.domain.models import RuleDefinition
from ai_dataops.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class CompiledQuery:
    sql: str
    params: tuple[Any, ...]
    bad_condition: str | None = None


def _literal(value: object) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _condition(rule: RuleDefinition, regex_function: str = "REGEXP_MATCHES") -> str:
    config = rule.config

    if rule.rule_type in {"not_null", "unique"}:
        column = _require_column(rule)
        return f"t.{column} IS NOT NULL"
    if rule.rule_type == "accepted_values":
        column = _require_column(rule)
        values = config.get("values")
        if not isinstance(values, list) or not values:
            raise ConfigurationError(f"{rule.rule_id}: accepted_values requires values[]")
        literals = ", ".join(_literal(value) for value in values)
        return f"t.{column} IN ({literals})"
    if rule.rule_type == "regex_match":
        column = _require_column(rule)
        pattern = config.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ConfigurationError(f"{rule.rule_id}: regex_match requires a pattern")
        return f"{regex_function}(t.{column}, {_literal(pattern)})"
    if rule.rule_type == "range":
        column = _require_column(rule)
        minimum = config.get("min")
        maximum = config.get("max")
        if minimum is None or maximum is None:
            raise ConfigurationError(f"{rule.rule_id}: range requires min and max")
        return f"t.{column} BETWEEN {_literal(minimum)} AND {_literal(maximum)}"
    if rule.rule_type == "expression":
        predicate = config.get("predicate")
        if not isinstance(predicate, str) or not predicate.strip():
            raise ConfigurationError(f"{rule.rule_id}: expression requires a predicate")
        if any(token in predicate for token in (";", "--", "/*")):
            raise ConfigurationError(f"{rule.rule_id}: predicate contains forbidden tokens")
        return f"({predicate})"
    if rule.rule_type == "foreign_key":
        column = _require_column(rule)
        ref_dataset = config.get("ref_dataset")
        ref_column = config.get("ref_column")
        if not isinstance(ref_dataset, str) or not isinstance(ref_column, str):
            raise ConfigurationError(
                f"{rule.rule_id}: foreign_key requires ref_dataset and ref_column"
            )
        return (
            f"(t.{column} IS NULL OR EXISTS ("
            f"SELECT 1 FROM {ref_dataset} r WHERE r.{ref_column} = t.{column}))"
        )
    raise ConfigurationError(f"{rule.rule_id}: {rule.rule_type} is not a row rule")


def _require_column(rule: RuleDefinition) -> str:
    if len(rule.columns) != 1:
        raise ConfigurationError(f"{rule.rule_id}: exactly one column is required")
    return rule.columns[0]


def compile_row_rule(
    rule: RuleDefinition,
    table: str,
    data_date: date,
    *,
    regex_function: str = "REGEXP_MATCHES",
) -> CompiledQuery:
    if rule.rule_type == "unique":
        column = _require_column(rule)
        sql = (
            f"SELECT COUNT(*) AS records_evaluated, "
            f"COUNT(DISTINCT t.{column}) AS records_passed "
            f"FROM {table} t WHERE t.order_date = ?"
        )
        return CompiledQuery(sql, (data_date,))

    condition = _condition(rule, regex_function)
    sql = (
        "SELECT COUNT(*) AS records_evaluated, "
        f"COALESCE(SUM(CASE WHEN {condition} THEN 1 ELSE 0 END), 0) AS records_passed, "
        f"COALESCE(SUM(CASE WHEN NOT ({condition}) THEN 1 ELSE 0 END), 0) AS records_failed "
        f"FROM {table} t WHERE t.order_date = ?"
    )
    return CompiledQuery(sql, (data_date,), bad_condition=condition)


def compile_samples(
    rule: RuleDefinition,
    table: str,
    data_date: date,
    *,
    regex_function: str = "REGEXP_MATCHES",
) -> CompiledQuery | None:
    if rule.rule_type in {"freshness", "schema_contract"}:
        return None
    if rule.rule_type == "unique":
        column = _require_column(rule)
        sql = (
            f"SELECT t.{column} AS sample FROM {table} t WHERE t.order_date = ? "
            f"GROUP BY t.{column} HAVING COUNT(*) > 1 LIMIT 3"
        )
        return CompiledQuery(sql, (data_date,))
    condition = _condition(rule, regex_function)
    column = _require_column(rule) if rule.columns else "order_id"
    sql = (
        f"SELECT t.{column} AS sample FROM {table} t "
        f"WHERE t.order_date = ? AND NOT ({condition}) LIMIT 3"
    )
    return CompiledQuery(sql, (data_date,))


def compile_freshness(rule: RuleDefinition, table: str, execution_id: str) -> CompiledQuery:
    del rule
    sql = (
        f"SELECT MAX(t.order_date) AS max_date, COUNT(*) AS row_count "
        f"FROM {table} t WHERE t.execution_id = ?"
    )
    return CompiledQuery(sql, (execution_id,))


__all__ = [
    "CompiledQuery",
    "compile_freshness",
    "compile_row_rule",
    "compile_samples",
]
