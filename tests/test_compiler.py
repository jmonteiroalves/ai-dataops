"""Rule compilation, including configuration errors."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from ai_dataops.config import Settings
from ai_dataops.domain.models import RuleDefinition
from ai_dataops.errors import ConfigurationError
from ai_dataops.quality.catalog import load_rules
from ai_dataops.quality.compiler import (
    compile_freshness,
    compile_row_rule,
    compile_samples,
)

DAY = date(2026, 11, 18)


@pytest.fixture
def completeness_rule(demo_settings: Settings) -> RuleDefinition:
    rules = load_rules(demo_settings.rules_dir, demo_settings)
    return next(item for item in rules if item.rule_id == "orders_completeness_customer_id")


def test_not_null_compiles_to_a_count_query(completeness_rule: RuleDefinition) -> None:
    compiled = compile_row_rule(completeness_rule, "demo.sales_clean", DAY)

    assert "COUNT(*)" in compiled.sql
    assert "customer_id IS NOT NULL" in compiled.sql
    assert compiled.params == (DAY,)


def test_regex_compiles_to_the_dialect_function(completeness_rule: RuleDefinition) -> None:
    rule = replace(
        completeness_rule,
        rule_type="regex_match",
        columns=("order_id",),
        config={"pattern": "^O[0-9]+"},
    )

    compiled = compile_row_rule(rule, "demo.sales_clean", DAY)

    assert "REGEXP_MATCHES" in compiled.sql


def test_unique_uses_distinct_count(completeness_rule: RuleDefinition) -> None:
    rule = replace(completeness_rule, rule_type="unique", columns=("order_id",), config={})

    compiled = compile_row_rule(rule, "demo.sales_clean", DAY)

    assert "COUNT(DISTINCT" in compiled.sql
    assert compile_samples(rule, "demo.sales_clean", DAY) is not None


def test_samples_are_not_generated_for_freshness(completeness_rule: RuleDefinition) -> None:
    rule = replace(completeness_rule, rule_type="freshness", columns=(), config={})

    assert compile_samples(rule, "demo.sales_clean", DAY) is None
    compiled = compile_freshness(rule, "demo.sales_clean", "exec-1")
    assert compiled.params == ("exec-1",)


@pytest.mark.parametrize(
    "overrides",
    [
        {"rule_type": "accepted_values", "columns": ("status",), "config": {}},
        {"rule_type": "regex_match", "columns": ("status",), "config": {}},
        {"rule_type": "range", "columns": ("price",), "config": {"min": 0}},
        {"rule_type": "expression", "columns": ("price",), "config": {"predicate": "1=1; drop"}},
        {"rule_type": "expression", "columns": ("price",), "config": {}},
        {"rule_type": "foreign_key", "columns": ("customer_id",), "config": {}},
        {"rule_type": "not_null", "columns": ("a", "b"), "config": {}},
    ],
)
def test_invalid_configurations_are_rejected(
    completeness_rule: RuleDefinition, overrides: dict[str, object]
) -> None:
    broken = replace(completeness_rule, **overrides)  # type: ignore[arg-type]

    with pytest.raises(ConfigurationError):
        compile_row_rule(broken, "demo.sales_clean", DAY)
