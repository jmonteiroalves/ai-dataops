"""Warehouse schema for both engines, generated from one specification.

DuckDB and BigQuery share table and column names. DuckDB gets primary keys and indexes
(for local idempotency and speed); BigQuery gets partitioning, clustering and logical keys
enforced by MERGE (ADR-006).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ai_dataops.config import Engine, Settings
from ai_dataops.domain.ports import Warehouse
from ai_dataops.errors import ConfigurationError

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")

STRING = "string"
INT = "int"
FLOAT = "float"
BOOL = "bool"
DATE = "date"
TIMESTAMP = "timestamp"
JSON = "json"

_DUCK_TYPES = {
    STRING: "VARCHAR",
    INT: "BIGINT",
    FLOAT: "DOUBLE",
    BOOL: "BOOLEAN",
    DATE: "DATE",
    TIMESTAMP: "TIMESTAMP",
    JSON: "VARCHAR",
}
_BQ_TYPES = {
    STRING: "STRING",
    INT: "INT64",
    FLOAT: "FLOAT64",
    BOOL: "BOOL",
    DATE: "DATE",
    TIMESTAMP: "TIMESTAMP",
    JSON: "STRING",
}


@dataclass(frozen=True, slots=True)
class Table:
    name: str
    columns: tuple[tuple[str, str, bool], ...]
    key: tuple[str, ...] = ()
    partition: str | None = None
    cluster: tuple[str, ...] = ()
    require_filter: bool = False
    indexes: tuple[tuple[str, ...], ...] = field(default_factory=tuple)

    @property
    def baseline(self) -> str:
        return self.name.split(".", 1)[0]

    @property
    def short_name(self) -> str:
        return self.name.split(".", 1)[1]


def _columns(*items: tuple[str, str, bool]) -> tuple[tuple[str, str, bool], ...]:
    return items


def _tables(settings: Settings) -> list[Table]:
    meta = settings.bq_meta_dataset
    obs = settings.bq_obs_dataset
    quality = settings.bq_quality_dataset
    detect = settings.bq_detect_dataset
    ai = settings.bq_ai_dataset
    demo = settings.demo_dataset

    return [
        # ---------------------------------------------------------------- meta
        Table(
            f"{meta}.pipelines",
            _columns(
                ("pipeline_id", STRING, True),
                ("name", STRING, True),
                ("domain", STRING, True),
                ("owner", STRING, False),
                ("criticality", STRING, True),
                ("schedule_cron", STRING, False),
                ("expected_runtime_seconds", FLOAT, False),
                ("freshness_sla_minutes", INT, False),
                ("is_active", BOOL, True),
                ("created_at", TIMESTAMP, True),
                ("updated_at", TIMESTAMP, True),
            ),
            key=("pipeline_id",),
        ),
        Table(
            f"{meta}.datasets",
            _columns(
                ("dataset_key", STRING, True),
                ("layer", STRING, True),
                ("domain", STRING, True),
                ("owner", STRING, False),
                ("criticality", STRING, True),
                ("freshness_sla_minutes", INT, False),
                ("row_identifier", STRING, False),
                ("is_active", BOOL, True),
                ("created_at", TIMESTAMP, True),
                ("updated_at", TIMESTAMP, True),
            ),
            key=("dataset_key",),
        ),
        Table(
            f"{meta}.pipeline_datasets",
            _columns(
                ("pipeline_id", STRING, True),
                ("dataset_key", STRING, True),
                ("role", STRING, True),
            ),
            key=("pipeline_id", "dataset_key", "role"),
        ),
        Table(
            f"{meta}.dataset_lineage",
            _columns(
                ("edge_id", STRING, True),
                ("upstream_dataset_key", STRING, True),
                ("downstream_dataset_key", STRING, True),
                ("relation_type", STRING, True),
                ("valid_from", TIMESTAMP, True),
                ("valid_to", TIMESTAMP, False),
            ),
            key=("edge_id",),
        ),
        Table(
            f"{meta}.quality_rules",
            _columns(
                ("rule_id", STRING, True),
                ("name", STRING, True),
                ("dataset_key", STRING, True),
                ("dimension", STRING, True),
                ("current_version", INT, True),
                ("severity", STRING, True),
                ("owner", STRING, False),
                ("is_active", BOOL, True),
                ("created_at", TIMESTAMP, True),
                ("updated_at", TIMESTAMP, True),
            ),
            key=("rule_id",),
        ),
        Table(
            f"{meta}.quality_rule_versions",
            _columns(
                ("rule_id", STRING, True),
                ("version", INT, True),
                ("rule_type", STRING, True),
                ("columns_json", JSON, True),
                ("config_json", JSON, True),
                ("config_hash", STRING, True),
                ("threshold", FLOAT, True),
                ("warn_threshold", FLOAT, False),
                ("weight", FLOAT, True),
                ("severity", STRING, True),
                ("change_reason", STRING, False),
                ("created_by", STRING, False),
                ("valid_from", TIMESTAMP, True),
                ("valid_to", TIMESTAMP, False),
            ),
            key=("rule_id", "version"),
        ),
        Table(
            f"{meta}.scenarios",
            _columns(
                ("scenario_id", STRING, True),
                ("name", STRING, True),
                ("description", STRING, True),
                ("config_json", JSON, True),
                ("expected_signal", STRING, False),
                ("is_active", BOOL, True),
            ),
            key=("scenario_id",),
        ),
        Table(
            f"{meta}.prompt_versions",
            _columns(
                ("prompt_id", STRING, True),
                ("version", INT, True),
                ("model_id", STRING, True),
                ("system_instructions", STRING, True),
                ("template", STRING, True),
                ("output_schema_version", STRING, True),
                ("content_hash", STRING, True),
                ("is_active", BOOL, True),
                ("created_at", TIMESTAMP, True),
            ),
            key=("prompt_id", "version"),
        ),
        Table(
            f"{meta}.llm_models",
            _columns(
                ("model_id", STRING, True),
                ("provider", STRING, True),
                ("display_name", STRING, True),
                ("input_price_per_mtok", FLOAT, True),
                ("output_price_per_mtok", FLOAT, True),
                ("valid_from", TIMESTAMP, True),
                ("valid_to", TIMESTAMP, False),
            ),
            key=("model_id", "valid_from"),
        ),
        Table(
            f"{meta}.run_locks",
            _columns(
                ("run_key", STRING, True),
                ("acquired_at", TIMESTAMP, True),
                ("expires_at", TIMESTAMP, True),
                ("owner", STRING, True),
            ),
            key=("run_key",),
        ),
        # ------------------------------------------------------------ observed
        Table(
            f"{obs}.pipeline_executions",
            _columns(
                ("execution_id", STRING, True),
                ("pipeline_id", STRING, True),
                ("logical_date", DATE, True),
                ("attempt", INT, True),
                ("scenario_id", STRING, False),
                ("status", STRING, True),
                ("started_at", TIMESTAMP, True),
                ("finished_at", TIMESTAMP, False),
                ("duration_seconds", FLOAT, False),
                ("records_read", INT, True),
                ("records_processed", INT, True),
                ("records_rejected", INT, True),
                ("rejection_rate", FLOAT, True),
                ("error_code", STRING, False),
                ("error_message", STRING, False),
                ("triggered_by", STRING, True),
                ("execution_metadata", JSON, True),
                ("created_at", TIMESTAMP, True),
            ),
            key=("execution_id",),
            partition="logical_date",
            cluster=("pipeline_id",),
            indexes=(("logical_date",),),
        ),
        Table(
            f"{obs}.execution_steps",
            _columns(
                ("execution_id", STRING, True),
                ("step_name", STRING, True),
                ("status", STRING, True),
                ("started_at", TIMESTAMP, True),
                ("finished_at", TIMESTAMP, False),
                ("duration_ms", INT, False),
                ("details", JSON, False),
            ),
            key=("execution_id", "step_name"),
        ),
        Table(
            f"{obs}.execution_log_events",
            _columns(
                ("event_id", STRING, True),
                ("execution_id", STRING, True),
                ("event_at", TIMESTAMP, True),
                ("level", STRING, True),
                ("message", STRING, True),
                ("context", JSON, False),
            ),
            key=("event_id",),
            partition="event_at",
            cluster=("execution_id",),
        ),
        Table(
            f"{obs}.schema_snapshots",
            _columns(
                ("snapshot_id", STRING, True),
                ("dataset_key", STRING, True),
                ("execution_id", STRING, False),
                ("captured_at", TIMESTAMP, True),
                ("column_name", STRING, True),
                ("data_type", STRING, True),
                ("is_nullable", BOOL, True),
                ("ordinal_position", INT, True),
                ("schema_hash", STRING, True),
            ),
            key=("snapshot_id",),
            partition="captured_at",
            cluster=("dataset_key",),
        ),
        Table(
            f"{obs}.metric_observations",
            _columns(
                ("observation_id", STRING, True),
                ("entity_type", STRING, True),
                ("entity_id", STRING, True),
                ("metric_name", STRING, True),
                ("value", FLOAT, True),
                ("unit", STRING, True),
                ("execution_id", STRING, False),
                ("observed_at", TIMESTAMP, True),
                ("dimensions", JSON, True),
                ("is_valid", BOOL, True),
            ),
            key=("observation_id",),
            partition="observed_at",
            cluster=("entity_id", "metric_name"),
            indexes=(("entity_id", "metric_name", "observed_at"),),
        ),
        Table(
            f"{obs}.stage_runs",
            _columns(
                ("stage_run_id", STRING, True),
                ("run_key", STRING, True),
                ("stage_name", STRING, True),
                ("status", STRING, True),
                ("started_at", TIMESTAMP, True),
                ("finished_at", TIMESTAMP, False),
                ("duration_ms", INT, False),
                ("error_message", STRING, False),
                ("created_at", TIMESTAMP, True),
            ),
            key=("stage_run_id",),
            partition="started_at",
        ),
        # ------------------------------------------------------------- quality
        Table(
            f"{quality}.quality_rule_results",
            _columns(
                ("result_id", STRING, True),
                ("execution_id", STRING, True),
                ("rule_id", STRING, True),
                ("rule_version", INT, True),
                ("dataset_key", STRING, True),
                ("evaluated_at", TIMESTAMP, True),
                ("status", STRING, True),
                ("records_evaluated", INT, True),
                ("records_passed", INT, True),
                ("records_failed", INT, True),
                ("pass_rate", FLOAT, True),
                ("observed_value", FLOAT, True),
                ("threshold", FLOAT, True),
                ("severity", STRING, True),
                ("details", JSON, True),
                ("duration_ms", INT, True),
                ("created_at", TIMESTAMP, True),
            ),
            key=("result_id",),
            partition="evaluated_at",
            cluster=("dataset_key", "rule_id"),
        ),
        Table(
            f"{quality}.quality_scores",
            _columns(
                ("score_id", STRING, True),
                ("execution_id", STRING, True),
                ("dataset_key", STRING, True),
                ("dimension", STRING, True),
                ("rules_total", INT, True),
                ("rules_passed", INT, True),
                ("rules_failed", INT, True),
                ("rules_error", INT, True),
                ("score", FLOAT, False),
                ("weight_total", FLOAT, True),
                ("created_at", TIMESTAMP, True),
            ),
            key=("score_id",),
            partition="created_at",
        ),
        # -------------------------------------------------------------- detect
        Table(
            f"{detect}.anomalies",
            _columns(
                ("anomaly_id", STRING, True),
                ("entity_type", STRING, True),
                ("entity_id", STRING, True),
                ("metric_name", STRING, True),
                ("execution_id", STRING, False),
                ("detected_at", TIMESTAMP, True),
                ("window_start", TIMESTAMP, True),
                ("window_end", TIMESTAMP, True),
                ("observed_value", FLOAT, True),
                ("expected_value", FLOAT, True),
                ("lower_bound", FLOAT, True),
                ("upper_bound", FLOAT, True),
                ("deviation_abs", FLOAT, True),
                ("deviation_pct", FLOAT, False),
                ("score", FLOAT, True),
                ("method", STRING, True),
                ("method_version", STRING, True),
                ("sample_size", INT, True),
                ("severity", STRING, True),
                ("confidence", FLOAT, True),
                ("signal_type", STRING, True),
                ("status", STRING, True),
                ("explanation", JSON, True),
                ("created_at", TIMESTAMP, True),
            ),
            key=("anomaly_id",),
            partition="detected_at",
            cluster=("entity_id", "metric_name"),
        ),
        Table(
            f"{detect}.incidents",
            _columns(
                ("incident_id", STRING, True),
                ("signature", STRING, True),
                ("bucket_start", TIMESTAMP, True),
                ("title", STRING, True),
                ("primary_pipeline_id", STRING, False),
                ("primary_dataset_key", STRING, False),
                ("severity", STRING, True),
                ("status", STRING, True),
                ("opened_at", TIMESTAMP, True),
                ("closed_at", TIMESTAMP, False),
                ("evidence_count", INT, True),
                ("has_diagnosis", BOOL, True),
                ("correlation_method", STRING, True),
                ("hypotheses_json", JSON, True),
                ("created_at", TIMESTAMP, True),
                ("updated_at", TIMESTAMP, True),
            ),
            key=("incident_id",),
            partition="bucket_start",
        ),
        Table(
            f"{detect}.incident_events",
            _columns(
                ("incident_id", STRING, True),
                ("event_type", STRING, True),
                ("event_ref_id", STRING, True),
                ("linked_at", TIMESTAMP, True),
                ("weight", FLOAT, True),
                ("evidence", JSON, True),
            ),
            key=("incident_id", "event_type", "event_ref_id"),
        ),
        Table(
            f"{detect}.incident_status_history",
            _columns(
                ("history_id", STRING, True),
                ("incident_id", STRING, True),
                ("from_status", STRING, False),
                ("to_status", STRING, True),
                ("changed_by", STRING, True),
                ("reason", STRING, False),
                ("changed_at", TIMESTAMP, True),
            ),
            key=("history_id",),
            partition="changed_at",
        ),
        Table(
            f"{detect}.feedback_labels",
            _columns(
                ("label_id", STRING, True),
                ("signal_type", STRING, True),
                ("signal_id", STRING, True),
                ("label", STRING, True),
                ("reason", STRING, False),
                ("labeled_by", STRING, True),
                ("labeled_at", TIMESTAMP, True),
            ),
            key=("label_id",),
            partition="labeled_at",
        ),
        # ------------------------------------------------------------------ ai
        Table(
            f"{ai}.diagnoses",
            _columns(
                ("diagnosis_id", STRING, True),
                ("incident_id", STRING, True),
                ("prompt_id", STRING, True),
                ("prompt_version", INT, True),
                ("model_id", STRING, True),
                ("model_version", STRING, True),
                ("evidence_hash", STRING, True),
                ("requested_at", TIMESTAMP, True),
                ("responded_at", TIMESTAMP, False),
                ("latency_ms", INT, False),
                ("status", STRING, True),
                ("summary", STRING, False),
                ("observed_facts", JSON, False),
                ("affected_indicators", JSON, False),
                ("hypotheses", JSON, False),
                ("impact", JSON, False),
                ("investigation_steps", JSON, False),
                ("suggested_actions", JSON, False),
                ("confidence", FLOAT, False),
                ("limitations", JSON, False),
                ("insufficient_evidence", BOOL, True),
                ("raw_response", STRING, False),
                ("input_tokens", INT, False),
                ("output_tokens", INT, False),
                ("estimated_cost_usd", FLOAT, False),
                ("is_current", BOOL, True),
                ("superseded_by", STRING, False),
            ),
            key=("diagnosis_id",),
            partition="requested_at",
            cluster=("incident_id",),
        ),
        Table(
            f"{ai}.diagnosis_evidence",
            _columns(
                ("diagnosis_id", STRING, True),
                ("evidence_id", STRING, True),
                ("evidence_type", STRING, True),
                ("ref_id", STRING, False),
                ("payload_excerpt", STRING, False),
            ),
            key=("diagnosis_id", "evidence_id"),
        ),
        Table(
            f"{ai}.ai_requests",
            _columns(
                ("request_id", STRING, True),
                ("incident_id", STRING, True),
                ("diagnosis_id", STRING, False),
                ("outcome", STRING, True),
                ("latency_ms", INT, False),
                ("input_tokens", INT, False),
                ("output_tokens", INT, False),
                ("estimated_cost_usd", FLOAT, False),
                ("error_code", STRING, False),
                ("created_at", TIMESTAMP, True),
            ),
            key=("request_id",),
            partition="created_at",
        ),
        Table(
            f"{ai}.recommendations",
            _columns(
                ("recommendation_id", STRING, True),
                ("diagnosis_id", STRING, True),
                ("incident_id", STRING, True),
                ("category", STRING, True),
                ("action", STRING, True),
                ("priority", STRING, True),
                ("status", STRING, True),
                ("owner", STRING, False),
                ("created_at", TIMESTAMP, True),
                ("resolved_at", TIMESTAMP, False),
            ),
            key=("recommendation_id",),
            partition="created_at",
        ),
        # ---------------------------------------------------------------- demo
        Table(
            f"{demo}.dim_customers",
            _columns(
                ("customer_id", STRING, True),
                ("customer_name", STRING, True),
                ("segment", STRING, True),
                ("region", STRING, True),
                ("created_at", TIMESTAMP, True),
            ),
            key=("customer_id",),
        ),
        Table(
            f"{demo}.sales_raw",
            _columns(
                ("row_id", STRING, True),
                ("execution_id", STRING, True),
                ("order_id", STRING, True),
                ("customer_id", STRING, False),
                ("order_ts", TIMESTAMP, True),
                ("order_date", DATE, True),
                ("status", STRING, True),
                ("payment_method", STRING, True),
                ("channel", STRING, True),
                ("unit_price", FLOAT, True),
                ("quantity", INT, True),
                ("discount", FLOAT, False),
                ("order_total", FLOAT, True),
                ("source_file", STRING, True),
                ("ingested_at", TIMESTAMP, True),
            ),
            partition="order_date",
            cluster=("order_id",),
        ),
        Table(
            f"{demo}.sales_clean",
            _columns(
                ("row_id", STRING, True),
                ("execution_id", STRING, True),
                ("order_id", STRING, True),
                ("customer_id", STRING, False),
                ("order_ts", TIMESTAMP, True),
                ("order_date", DATE, True),
                ("status", STRING, True),
                ("payment_method", STRING, True),
                ("channel", STRING, True),
                ("unit_price", FLOAT, True),
                ("quantity", INT, True),
                ("discount", FLOAT, False),
                ("order_total", FLOAT, True),
                ("ingested_at", TIMESTAMP, True),
            ),
            partition="order_date",
            cluster=("order_id",),
        ),
        Table(
            f"{demo}.sales_rejected",
            _columns(
                ("rejection_id", STRING, True),
                ("execution_id", STRING, True),
                ("order_id", STRING, False),
                ("order_date", DATE, False),
                ("reason", STRING, True),
                ("payload", JSON, True),
                ("rejected_at", TIMESTAMP, True),
            ),
            partition="order_date",
        ),
        Table(
            f"{demo}.sales_daily_metrics",
            _columns(
                ("order_date", DATE, True),
                ("execution_id", STRING, True),
                ("orders_count", INT, True),
                ("gross_revenue", FLOAT, True),
                ("avg_order_value", FLOAT, True),
                ("unique_customers", INT, True),
                ("cancelled_orders", INT, True),
                ("created_at", TIMESTAMP, True),
            ),
            partition="order_date",
        ),
    ]


def table_specs(settings: Settings) -> list[Table]:
    return _tables(settings)


def primary_keys(settings: Settings) -> dict[str, tuple[str, ...]]:
    return {table.name: table.key for table in _tables(settings) if table.key}


def column_types(settings: Settings) -> dict[tuple[str, str], str]:
    return {
        (table.name, name): _BQ_TYPES[kind]
        for table in _tables(settings)
        for name, kind, _ in table.columns
    }


def schema_statements(settings: Settings) -> list[str]:
    """DuckDB DDL (schemas, tables with primary keys, and lookup indexes)."""
    tables = _tables(settings)
    statements: list[str] = []
    for dataset in sorted({table.baseline for table in tables}):
        statements.append(f"CREATE SCHEMA IF NOT EXISTS {_identifier(dataset)}")
    for table in tables:
        columns = ",\n            ".join(
            f"{_identifier(name)} {_DUCK_TYPES[kind]}" + (" NOT NULL" if required else "")
            for name, kind, required in table.columns
        )
        constraint = (
            f",\n            PRIMARY KEY ({', '.join(_identifier(column) for column in table.key)})"
            if table.key
            else ""
        )
        statements.append(
            f"CREATE TABLE IF NOT EXISTS {_qualified(table.name)} (\n"
            f"            {columns}{constraint}\n        )"
        )
        for index_columns in table.indexes:
            suffix = "_".join(index_columns)
            statements.append(
                f"CREATE INDEX IF NOT EXISTS idx_{table.short_name}_{suffix} "
                f"ON {_qualified(table.name)} ({', '.join(index_columns)})"
            )
    return statements


def bigquery_schema_statements(settings: Settings) -> list[str]:
    """BigQuery DDL (datasets, partitioned/clustered tables, logical keys only)."""
    tables = _tables(settings)
    statements: list[str] = []
    for dataset in sorted({table.baseline for table in tables}):
        statements.append(
            f"CREATE SCHEMA IF NOT EXISTS `{_identifier(dataset)}` "
            f"OPTIONS(location='{settings.region}')"
        )
    for table in tables:
        columns = ",\n  ".join(
            f"{_identifier(name)} {_BQ_TYPES[kind]}" + (" NOT NULL" if required else "")
            for name, kind, required in table.columns
        )
        options = ["require_partition_filter=true"] if table.require_filter else []
        if table.partition:
            kind = dict((name, kind) for name, kind, _ in table.columns)[table.partition]
            expression = (
                f"DATE({_identifier(table.partition)})"
                if kind == TIMESTAMP
                else _identifier(table.partition)
            )
            options.append("partition_expiration_days=400")
            suffix = f" PARTITION BY {expression}"
        else:
            suffix = ""
        if table.cluster:
            suffix += f" CLUSTER BY {', '.join(_identifier(c) for c in table.cluster)}"
        if options:
            suffix += f" OPTIONS({', '.join(options)})"
        statements.append(f"CREATE TABLE IF NOT EXISTS `{table.name}` (\n  {columns}\n){suffix}")
    return statements


def initialize_schema(warehouse: Warehouse, settings: Settings) -> None:
    statements = (
        bigquery_schema_statements(settings)
        if settings.engine is Engine.BIGQUERY
        else schema_statements(settings)
    )
    for statement in statements:
        warehouse.execute(statement)


def _identifier(name: str) -> str:
    if not _IDENTIFIER.fullmatch(name):
        raise ConfigurationError(f"invalid SQL identifier: {name!r}")
    return name


def _qualified(name: str) -> str:
    parts = name.split(".")
    if len(parts) != 2:
        raise ConfigurationError(f"table must be schema-qualified: {name!r}")
    return ".".join(_identifier(part) for part in parts)


__all__ = [
    "Table",
    "bigquery_schema_statements",
    "column_types",
    "initialize_schema",
    "primary_keys",
    "schema_statements",
    "table_specs",
]
