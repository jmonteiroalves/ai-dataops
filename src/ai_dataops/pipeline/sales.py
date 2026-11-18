"""The demo sales pipeline: extract -> clean -> aggregate.

The pipeline is intentionally realistic in one specific way: invalid records are
never dropped silently. Every rejected row is persisted with a reason, and every
run writes the counters the observability layer consumes.
"""

from __future__ import annotations

import json
import statistics
from datetime import date, datetime
from typing import Any

from ai_dataops.config import Settings
from ai_dataops.domain.ports import Warehouse
from ai_dataops.errors import ExternalServiceError, NotFoundError, SchemaError
from ai_dataops.ids import deterministic_id
from ai_dataops.instrumentation.recorder import ExecutionRecorder
from ai_dataops.pipeline.scenarios import scenario
from ai_dataops.pipeline.synthetic import CONTRACT_COLUMNS, read_manifest, read_raw
from ai_dataops.timeutil import utcnow

PIPELINE_ID = "sales_daily"
_VALID_STATUS = frozenset({"completed", "cancelled", "pending"})
_VALID_PAYMENT = frozenset({"credit_card", "pix", "boleto", "debit_card"})
_HISTOGRAM_EDGES = [0.0, 50.0, 100.0, 200.0, 400.0, 800.0, 1600.0, 3200.0, 6400.0, 1e12]

_COLUMN_TYPES: dict[str, str] = {
    "order_id": "STRING",
    "customer_id": "STRING",
    "order_ts": "TIMESTAMP",
    "order_date": "DATE",
    "status": "STRING",
    "payment_method": "STRING",
    "channel": "STRING",
    "unit_price": "DOUBLE",
    "quantity": "BIGINT",
    "discount": "DOUBLE",
    "order_total": "DOUBLE",
}


def run_sales_pipeline(
    warehouse: Warehouse,
    settings: Settings,
    logical_date: date,
    *,
    scenario_id: str | None = None,
    attempt: int = 1,
    triggered_by: str = "manual",
) -> str:
    """Execute the pipeline for one logical date and return the execution id."""
    manifest = read_manifest(settings.landing_dir)
    entry = manifest.get(logical_date.isoformat(), {})
    effective_scenario = scenario_id or str(entry.get("scenario_id", "normal"))
    definition = scenario(effective_scenario)
    landing_path = settings.landing_dir / f"orders_{logical_date.isoformat()}.csv"
    if not landing_path.exists():
        raise NotFoundError(
            "landing file not found",
            context={"date": logical_date.isoformat(), "path": str(landing_path)},
        )

    data_date = date.fromisoformat(str(entry.get("data_date", logical_date.isoformat())))
    metadata: dict[str, Any] = {
        "data_date": data_date.isoformat(),
        "scenario_id": effective_scenario,
        "seed": entry.get("seed"),
        "simulated_delay_seconds": definition.simulated_delay_seconds,
        "schema_change": definition.schema_change,
    }

    demo = settings.demo_dataset
    raw_key = f"{demo}.sales_raw"
    clean_key = f"{demo}.sales_clean"
    daily_key = f"{demo}.sales_daily_metrics"

    with ExecutionRecorder(
        warehouse,
        PIPELINE_ID,
        logical_date,
        obs_dataset=settings.bq_obs_dataset,
        attempt=attempt,
        scenario_id=effective_scenario,
        triggered_by=triggered_by,
        metadata=metadata,
    ) as recorder:
        # ------------------------------------------------------------- extract
        extract_started = recorder.start_step("extract")
        header, rows = read_raw(landing_path)
        recorder.record_schema(raw_key, [(column, "STRING", True) for column in header])
        missing = [column for column in CONTRACT_COLUMNS if column not in header]
        unexpected = [column for column in header if column not in CONTRACT_COLUMNS]
        if missing or unexpected:
            recorder.set_counters(read=len(rows), processed=0, rejected=len(rows))
            recorder.finish_step("extract", extract_started, status="failed")
            raise SchemaError(
                "landing schema violates the contract",
                context={
                    "missing": missing,
                    "unexpected": unexpected,
                    "classification": "breaking",
                },
            )
        if definition.fail_after_fraction is not None:
            partial = int(len(rows) * definition.fail_after_fraction)
            recorder.set_counters(read=partial, processed=0, rejected=0)
            recorder.finish_step("extract", extract_started, status="failed")
            raise ExternalServiceError(
                "upstream extract timed out",
                context={"records_read": partial, "expected": len(rows)},
            )
        recorder.finish_step("extract", extract_started)

        # ------------------------------------------------------------- transform
        transform_started = recorder.start_step("transform")
        parsed, rejected = _parse(rows, data_date, recorder.execution_id)
        clean = [row for row in parsed if row["valid"]]

        warehouse.execute(f"DELETE FROM {demo}.sales_raw WHERE order_date = ?", (data_date,))
        warehouse.execute(f"DELETE FROM {demo}.sales_clean WHERE order_date = ?", (data_date,))
        warehouse.execute(f"DELETE FROM {demo}.sales_rejected WHERE order_date = ?", (data_date,))
        warehouse.execute(
            f"DELETE FROM {demo}.sales_daily_metrics WHERE order_date = ?", (data_date,)
        )

        _append(
            warehouse,
            raw_key,
            [_raw_row(row, recorder.execution_id, landing_path.name) for row in parsed],
        )
        _append(
            warehouse,
            clean_key,
            [_clean_row(row, recorder.execution_id) for row in clean],
        )
        _append(
            warehouse,
            f"{demo}.sales_rejected",
            [_rejection_row(row, recorder.execution_id) for row in rejected],
        )
        recorder.finish_step("transform", transform_started)

        # ------------------------------------------------------------- aggregate
        aggregate_started = recorder.start_step("aggregate")
        _write_daily_metrics(warehouse, daily_key, clean, data_date, recorder.execution_id)
        recorder.record_schema(
            clean_key,
            [
                (column, _COLUMN_TYPES[column], column == "customer_id")
                for column in CONTRACT_COLUMNS
            ],
        )
        recorder.finish_step("aggregate", aggregate_started)

        recorder.set_counters(read=len(rows), processed=len(clean), rejected=len(rejected))
        _emit_metrics(recorder, clean, rejected, data_date, logical_date, clean_key)

    return recorder.execution_id


def _parse(
    rows: list[dict[str, str]],
    data_date: date,
    execution_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parsed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        row_id = deterministic_id("row", execution_id, index)
        try:
            order_date = date.fromisoformat(row["order_date"])
            order_ts = datetime.fromisoformat(row["order_ts"])
            unit_price = float(row["unit_price"])
            quantity = int(row["quantity"])
            discount = float(row.get("discount") or 0.0)
            order_total = float(row["order_total"])
        except (KeyError, TypeError, ValueError) as exc:
            rejected.append(
                {
                    "row_id": row_id,
                    "reason": f"unparseable_row:{type(exc).__name__}",
                    "payload": row,
                    "order_id": row.get("order_id") or None,
                    "order_date": None,
                }
            )
            continue

        record: dict[str, Any] = {
            "row_id": row_id,
            "order_id": row.get("order_id", ""),
            "customer_id": row.get("customer_id") or None,
            "order_ts": order_ts,
            "order_date": order_date,
            "status": row.get("status", ""),
            "payment_method": row.get("payment_method", ""),
            "channel": row.get("channel", ""),
            "unit_price": unit_price,
            "quantity": quantity,
            "discount": discount,
            "order_total": order_total,
            "valid": True,
        }
        reason = _rejection_reason(record, data_date)
        if reason is not None:
            record["valid"] = False
            rejected.append(
                {
                    "row_id": row_id,
                    "reason": reason,
                    "payload": row,
                    "order_id": record["order_id"] or None,
                    "order_date": order_date,
                }
            )
        parsed.append(record)
    return parsed, rejected


def _rejection_reason(record: dict[str, Any], data_date: date) -> str | None:
    if not record["order_id"]:
        return "missing_order_id"
    if record["order_date"] != data_date:
        return "unexpected_order_date"
    if record["unit_price"] <= 0:
        return "invalid_unit_price"
    if record["quantity"] <= 0:
        return "invalid_quantity"
    if record["order_total"] < 0:
        return "invalid_order_total"
    if record["status"] not in _VALID_STATUS:
        return "invalid_status"
    if record["payment_method"] not in _VALID_PAYMENT:
        return "invalid_payment_method"
    return None


def _raw_row(row: dict[str, Any], execution_id: str, source_file: str) -> dict[str, Any]:
    return {
        "row_id": row["row_id"],
        "execution_id": execution_id,
        "order_id": row["order_id"],
        "customer_id": row["customer_id"],
        "order_ts": row["order_ts"],
        "order_date": row["order_date"],
        "status": row["status"],
        "payment_method": row["payment_method"],
        "channel": row["channel"],
        "unit_price": row["unit_price"],
        "quantity": row["quantity"],
        "discount": row["discount"],
        "order_total": row["order_total"],
        "source_file": source_file,
        "ingested_at": utcnow(),
    }


def _clean_row(row: dict[str, Any], execution_id: str) -> dict[str, Any]:
    return {
        "row_id": row["row_id"],
        "execution_id": execution_id,
        "order_id": row["order_id"],
        "customer_id": row["customer_id"],
        "order_ts": row["order_ts"],
        "order_date": row["order_date"],
        "status": row["status"],
        "payment_method": row["payment_method"],
        "channel": row["channel"],
        "unit_price": row["unit_price"],
        "quantity": row["quantity"],
        "discount": row["discount"],
        "order_total": row["order_total"],
        "ingested_at": utcnow(),
    }


def _append(warehouse: Warehouse, table: str, rows: list[dict[str, Any]]) -> None:
    append = getattr(warehouse, "append_rows", None)
    if append is not None:
        append(table, rows)
    else:
        warehouse.write_rows(table, rows)


def _rejection_row(row: dict[str, Any], execution_id: str) -> dict[str, Any]:
    return {
        "rejection_id": deterministic_id("rejection", execution_id, row["row_id"]),
        "execution_id": execution_id,
        "order_id": row["order_id"],
        "order_date": row["order_date"],
        "reason": row["reason"],
        "payload": json.dumps(row["payload"], default=str)[:1000],
        "rejected_at": utcnow(),
    }


def _write_daily_metrics(
    warehouse: Warehouse,
    daily_key: str,
    clean: list[dict[str, Any]],
    data_date: date,
    execution_id: str,
) -> None:
    count = len(clean)
    gross = sum(float(row["order_total"]) for row in clean)
    _append(
        warehouse,
        daily_key,
        [
            {
                "order_date": data_date,
                "execution_id": execution_id,
                "orders_count": count,
                "gross_revenue": round(gross, 2),
                "avg_order_value": round(gross / count, 2) if count else 0.0,
                "unique_customers": len(
                    {row["customer_id"] for row in clean if row["customer_id"] is not None}
                ),
                "cancelled_orders": sum(1 for row in clean if row["status"] == "cancelled"),
                "created_at": utcnow(),
            }
        ],
    )


def _emit_metrics(
    recorder: ExecutionRecorder,
    clean: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    data_date: date,
    logical_date: date,
    clean_key: str,
) -> None:
    count = len(clean)
    nulls = sum(1 for row in clean if row["customer_id"] is None)
    distinct_orders = len({row["order_id"] for row in clean})
    duplicates = max(0, count - distinct_orders)
    totals = [float(row["order_total"]) for row in clean]
    freshness = (logical_date - data_date).days * 1440.0

    recorder.record_metric("pipeline", PIPELINE_ID, "records_processed", count, "count")
    recorder.record_metric(
        "pipeline",
        PIPELINE_ID,
        "rejection_rate",
        len(rejected) / (count + len(rejected)) if (count + len(rejected)) else 0.0,
        "ratio",
        dimensions={
            "sample_size": count + len(rejected),
            "failures": len(rejected),
        },
    )
    recorder.record_metric("dataset", clean_key, "row_count", count, "count")
    recorder.record_metric(
        "column",
        f"{clean_key}.customer_id",
        "null_rate.customer_id",
        nulls / count if count else 0.0,
        "ratio",
        dimensions={"sample_size": count, "failures": nulls},
    )
    recorder.record_metric(
        "dataset",
        clean_key,
        "duplicate_rate",
        duplicates / count if count else 0.0,
        "ratio",
        dimensions={"sample_size": count, "failures": duplicates},
    )
    recorder.record_metric("dataset", clean_key, "freshness_lag_minutes", freshness, "minutes")
    if totals:
        recorder.record_metric(
            "column",
            f"{clean_key}.order_total",
            "mean.order_total",
            statistics.fmean(totals),
            "currency",
        )
        recorder.record_metric(
            "column",
            f"{clean_key}.order_total",
            "median.order_total",
            statistics.median(totals),
            "currency",
        )
    counts = [0 for _ in range(len(_HISTOGRAM_EDGES) - 1)]
    for value in totals:
        for index in range(len(counts)):
            if _HISTOGRAM_EDGES[index] <= value < _HISTOGRAM_EDGES[index + 1]:
                counts[index] += 1
                break
    recorder.record_metric(
        "column",
        f"{clean_key}.order_total",
        "histogram.order_total",
        float(count),
        "count",
        dimensions={"edges": _HISTOGRAM_EDGES, "counts": counts},
    )


__all__ = ["PIPELINE_ID", "run_sales_pipeline"]
