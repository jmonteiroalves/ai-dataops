"""Idempotent seed data for the demo environment."""

from __future__ import annotations

import random
from typing import Any

from ai_dataops.config import Settings
from ai_dataops.domain.ports import Warehouse
from ai_dataops.ids import deterministic_id
from ai_dataops.pipeline.scenarios import scenario_rows
from ai_dataops.pipeline.synthetic import customer_ids
from ai_dataops.timeutil import utcnow


def _dataset_keys(settings: Settings) -> dict[str, str]:
    demo = settings.demo_dataset
    return {
        "raw": f"{demo}.sales_raw",
        "clean": f"{demo}.sales_clean",
        "daily": f"{demo}.sales_daily_metrics",
        "customers": f"{demo}.dim_customers",
    }


def bootstrap(warehouse: Warehouse, settings: Settings) -> None:
    """Create/refresh reference metadata. Safe to call on every run."""
    now = utcnow()
    meta = settings.bq_meta_dataset
    keys = _dataset_keys(settings)

    warehouse.write_rows(
        f"{meta}.pipelines",
        [
            {
                "pipeline_id": "sales_daily",
                "name": "Daily sales pipeline",
                "domain": "sales",
                "owner": "data-platform",
                "criticality": "tier_1",
                "schedule_cron": "0 6 * * *",
                "expected_runtime_seconds": 60.0,
                "freshness_sla_minutes": 1560,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )

    datasets: list[dict[str, Any]] = [
        {
            "dataset_key": keys["raw"],
            "layer": "bronze",
            "domain": "sales",
            "owner": "data-platform",
            "criticality": "tier_1",
            "freshness_sla_minutes": 1560,
            "row_identifier": "order_id",
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "dataset_key": keys["clean"],
            "layer": "silver",
            "domain": "sales",
            "owner": "data-platform",
            "criticality": "tier_1",
            "freshness_sla_minutes": 1560,
            "row_identifier": "order_id",
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "dataset_key": keys["daily"],
            "layer": "gold",
            "domain": "sales",
            "owner": "data-platform",
            "criticality": "tier_2",
            "freshness_sla_minutes": 1560,
            "row_identifier": "order_date",
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "dataset_key": keys["customers"],
            "layer": "silver",
            "domain": "sales",
            "owner": "data-platform",
            "criticality": "tier_2",
            "freshness_sla_minutes": 10080,
            "row_identifier": "customer_id",
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
    ]
    warehouse.write_rows(f"{meta}.datasets", datasets)

    warehouse.write_rows(
        f"{meta}.pipeline_datasets",
        [
            {"pipeline_id": "sales_daily", "dataset_key": keys["raw"], "role": "source"},
            {"pipeline_id": "sales_daily", "dataset_key": keys["clean"], "role": "target"},
            {"pipeline_id": "sales_daily", "dataset_key": keys["daily"], "role": "target"},
            {
                "pipeline_id": "sales_daily",
                "dataset_key": keys["customers"],
                "role": "reference",
            },
        ],
    )

    edges = [
        (keys["raw"], keys["clean"], "derived_from"),
        (keys["clean"], keys["daily"], "aggregated_into"),
        (keys["customers"], keys["clean"], "referenced_by"),
    ]
    warehouse.write_rows(
        f"{meta}.dataset_lineage",
        [
            {
                "edge_id": deterministic_id("lineage", upstream, downstream, relation),
                "upstream_dataset_key": upstream,
                "downstream_dataset_key": downstream,
                "relation_type": relation,
                "valid_from": now,
                "valid_to": None,
            }
            for upstream, downstream, relation in edges
        ],
    )

    rows: list[dict[str, Any]] = []
    for item in scenario_rows():
        payload = dict(item)
        payload["config_json"] = item["config_json"]
        rows.append(payload)
    warehouse.write_rows(f"{meta}.scenarios", rows)

    _seed_customers(warehouse, settings)


def _seed_customers(warehouse: Warehouse, settings: Settings) -> None:
    demo = settings.demo_dataset
    existing = warehouse.query(f"SELECT COUNT(*) AS total FROM {demo}.dim_customers")
    if existing and int(existing[0]["total"]) >= 500:
        return
    warehouse.execute(f"DELETE FROM {demo}.dim_customers WHERE TRUE")
    rng = random.Random(7)
    segments = ("consumer", "smb", "enterprise")
    regions = ("southeast", "south", "northeast", "north", "midwest")
    now = utcnow()
    rows = [
        {
            "customer_id": customer_id,
            "customer_name": f"Customer {customer_id}",
            "segment": rng.choices(segments, weights=(0.7, 0.2, 0.1), k=1)[0],
            "region": rng.choice(regions),
            "created_at": now,
        }
        for customer_id in customer_ids()
    ]
    append = getattr(warehouse, "append_rows", None)
    if append is not None:
        append(f"{demo}.dim_customers", rows)
    else:
        warehouse.write_rows(f"{demo}.dim_customers", rows)


__all__ = ["bootstrap"]
