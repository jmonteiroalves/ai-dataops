"""Catalog validation and immutable versioning."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_dataops.adapters.duckdb import DuckDBWarehouse
from ai_dataops.config import Settings
from ai_dataops.errors import ConfigurationError
from ai_dataops.quality.catalog import load_rules, sync_rules

VALID = """
rule_id: test_rule
name: Test rule
dataset: sales_clean
dimension: completeness
rule_type: not_null
columns: [order_id]
severity: medium
threshold: 0.99
"""


def _settings_with(demo_settings: Settings, rules_dir: Path) -> Settings:
    return demo_settings.model_copy(update={"rules_dir": rules_dir})


def test_missing_rules_directory_is_rejected(demo_settings: Settings, tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_rules(tmp_path / "missing", demo_settings)


def test_missing_required_field_is_rejected(demo_settings: Settings, tmp_path: Path) -> None:
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "rule.yml").write_text("rule_id: x\nname: y\n", encoding="utf-8")

    with pytest.raises(ConfigurationError):
        load_rules(rules, demo_settings)


def test_unsupported_rule_type_is_rejected(demo_settings: Settings, tmp_path: Path) -> None:
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "rule.yml").write_text(
        VALID.replace("rule_type: not_null", "rule_type: telepathy"), encoding="utf-8"
    )

    with pytest.raises(ConfigurationError):
        load_rules(rules, demo_settings)


def test_invalid_dimension_is_rejected(demo_settings: Settings, tmp_path: Path) -> None:
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "rule.yml").write_text(
        VALID.replace("dimension: completeness", "dimension: vibes"), encoding="utf-8"
    )

    with pytest.raises(ConfigurationError):
        load_rules(rules, demo_settings)


def test_changed_rule_creates_a_new_version(
    warehouse: DuckDBWarehouse, demo_settings: Settings, tmp_path: Path
) -> None:
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    path = rules_dir / "rule.yml"
    path.write_text(VALID, encoding="utf-8")
    settings = _settings_with(demo_settings, rules_dir)

    first = sync_rules(warehouse, settings, load_rules(rules_dir, settings))
    assert first[0].version == 1

    path.write_text(VALID.replace("threshold: 0.99", "threshold: 0.95"), encoding="utf-8")
    second = sync_rules(warehouse, settings, load_rules(rules_dir, settings))

    assert second[0].version == 2
    rows = warehouse.query(
        f"SELECT COUNT(*) AS n FROM {settings.bq_meta_dataset}.quality_rule_versions "
        "WHERE rule_id = 'test_rule'"
    )[0]["n"]
    assert rows == 2
