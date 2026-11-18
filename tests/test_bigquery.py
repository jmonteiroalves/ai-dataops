"""BigQuery schema generation and dialect handling (no network)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from ai_dataops.adapters.schema import (
    bigquery_schema_statements,
    column_types,
    primary_keys,
    table_specs,
)
from ai_dataops.config import Engine, Settings
from ai_dataops.quality.catalog import load_rules
from ai_dataops.quality.compiler import compile_row_rule

DAY = date(2026, 11, 18)


def _bigquery_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "engine": Engine.BIGQUERY,
            "project_id": "ai-dataops-demo-jma",
        }
    )


def test_bigquery_ddl_uses_cloud_types_and_no_primary_keys(demo_settings: Settings) -> None:
    statements = bigquery_schema_statements(_bigquery_settings(demo_settings))
    joined = "\n".join(statements)

    assert "INT64" in joined
    assert "FLOAT64" in joined
    assert "VARCHAR" not in joined
    assert "PRIMARY KEY" not in joined
    assert "CREATE SCHEMA IF NOT EXISTS `dataops_obs`" in joined
    assert "OPTIONS(location='us-central1')" in joined


def test_bigquery_partitioning_and_clustering(demo_settings: Settings) -> None:
    statements = bigquery_schema_statements(_bigquery_settings(demo_settings))
    observations = next(item for item in statements if "`dataops_obs.metric_observations`" in item)
    executions = next(item for item in statements if "`dataops_obs.pipeline_executions`" in item)

    assert "PARTITION BY DATE(observed_at)" in observations
    assert "CLUSTER BY entity_id, metric_name" in observations
    assert "PARTITION BY DATE(observed_at)" in observations
    assert "PARTITION BY logical_date" in executions
    assert "CLUSTER BY pipeline_id" in executions


def test_logical_keys_and_column_types_cover_every_table(demo_settings: Settings) -> None:
    settings = _bigquery_settings(demo_settings)
    tables = table_specs(settings)
    keys = primary_keys(settings)
    types = column_types(settings)

    assert len(tables) >= 25
    assert keys["dataops_obs.pipeline_executions"] == ("execution_id",)
    assert ("dataops_obs.metric_observations", "value") in types
    assert types[("dataops_obs.metric_observations", "value")] == "FLOAT64"
    assert types[("dataops_meta.quality_rule_versions", "config_json")] == "STRING"


def test_regex_dialect_switches_for_bigquery(demo_settings: Settings) -> None:
    rules = load_rules(demo_settings.rules_dir, demo_settings)
    base = next(item for item in rules if item.rule_id == "orders_completeness_customer_id")
    rule = replace(
        base,
        rule_type="regex_match",
        columns=("order_id",),
        config={"pattern": "^O[0-9]+"},
    )

    duck = compile_row_rule(rule, "demo.sales_clean", DAY)
    cloud = compile_row_rule(rule, "demo.sales_clean", DAY, regex_function="REGEXP_CONTAINS")

    assert "REGEXP_MATCHES" in duck.sql
    assert "REGEXP_CONTAINS" in cloud.sql


def test_merge_statement_uses_logical_keys() -> None:
    from ai_dataops.adapters.bigquery import build_merge_statement

    sql = build_merge_statement(
        "dataops_obs.pipeline_executions",
        ["execution_id", "status", "records_read"],
        ["execution_id"],
        2,
    )

    assert "MERGE `dataops_obs.pipeline_executions` T" in sql
    assert "ON T.execution_id = S.execution_id" in sql
    assert "UPDATE SET status = S.status, records_read = S.records_read" in sql
    assert "INSERT (execution_id, status, records_read)" in sql
    assert "@p0 AS execution_id" in sql
    assert "@p5 AS records_read" in sql


def test_parameter_serialisation_and_type_inference() -> None:
    from datetime import datetime

    from ai_dataops.adapters.bigquery import BigQueryWarehouse, _json_value, _param_value

    assert _json_value({"a": 1}, "STRING") == '{"a": 1}'
    assert _json_value(datetime(2026, 11, 18, 12), "TIMESTAMP") == "2026-11-18 12:00:00"
    assert _json_value(date(2026, 11, 18), "DATE") == "2026-11-18"
    assert _param_value({"a": 1}) == '{"a": 1}'
    assert isinstance(_param_value(datetime(2026, 11, 18)), datetime)
    assert _json_value(10, "INT64") == 10
    assert BigQueryWarehouse._infer_type(None, True) == "BOOL"
    assert BigQueryWarehouse._infer_type(None, 1) == "INT64"
    assert BigQueryWarehouse._infer_type(None, 1.5) == "FLOAT64"
    assert BigQueryWarehouse._infer_type(None, date(2026, 11, 18)) == "DATE"
    assert BigQueryWarehouse._infer_type(None, datetime(2026, 11, 18)) == "TIMESTAMP"
    assert BigQueryWarehouse._infer_type(None, "x") == "STRING"


def test_duckdb_schema_still_has_primary_keys(demo_settings: Settings) -> None:
    from ai_dataops.adapters.schema import schema_statements

    statements = "\n".join(schema_statements(demo_settings))

    assert "VARCHAR" in statements
    assert "PRIMARY KEY (execution_id)" in statements
    assert "CREATE INDEX IF NOT EXISTS" in statements
    assert "INT64" not in statements


def test_merge_statement_handles_key_only_tables() -> None:
    from ai_dataops.adapters.bigquery import build_merge_statement

    sql = build_merge_statement(
        "dataops_meta.pipeline_datasets",
        ["pipeline_id", "dataset_key", "role"],
        ["pipeline_id", "dataset_key", "role"],
        1,
    )

    assert "WHEN MATCHED" not in sql
    assert "WHEN NOT MATCHED THEN INSERT" in sql
