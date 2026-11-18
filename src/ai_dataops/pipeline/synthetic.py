"""Seeded synthetic sales data.

The generator writes a landing CSV per logical date plus a manifest describing
the scenario and its parameters. The pipeline consumes the landing files, so
schema changes are exercised at the file boundary rather than by mutating the
warehouse table.
"""

from __future__ import annotations

import csv
import json
import random
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ai_dataops.pipeline.scenarios import DayPlan, scenario, stale_data_date

CONTRACT_COLUMNS: tuple[str, ...] = (
    "order_id",
    "customer_id",
    "order_ts",
    "order_date",
    "status",
    "payment_method",
    "channel",
    "unit_price",
    "quantity",
    "discount",
    "order_total",
)

SCHEMA_CHANGE_COLUMNS: tuple[str, ...] = tuple(
    "discount_pct" if column == "discount" else column for column in CONTRACT_COLUMNS
)

_WEEKDAY_FACTOR: dict[int, float] = {
    0: 0.95,  # Monday
    1: 1.00,
    2: 1.02,
    3: 1.05,
    4: 1.15,
    5: 1.25,
    6: 0.85,
}

_PRODUCTS: tuple[tuple[str, float], ...] = (
    ("electronics", 450.0),
    ("home", 120.0),
    ("fashion", 80.0),
    ("sports", 200.0),
    ("books", 35.0),
)

MANIFEST_NAME = "manifest.json"


def customer_ids(count: int = 500) -> list[str]:
    return [f"C{index:05d}" for index in range(1, count + 1)]


def _manifest_path(landing_dir: Path) -> Path:
    return landing_dir / MANIFEST_NAME


def read_manifest(landing_dir: Path) -> dict[str, dict[str, Any]]:
    path = _manifest_path(landing_dir)
    if not path.exists():
        return {}
    loaded: dict[str, dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def _write_manifest(landing_dir: Path, entries: dict[str, dict[str, Any]]) -> None:
    _manifest_path(landing_dir).write_text(
        json.dumps(entries, indent=2, sort_keys=True), encoding="utf-8"
    )


def generate_day(
    landing_dir: Path,
    plan: DayPlan,
    *,
    volume: int,
) -> Path:
    """Generate the landing CSV and manifest entry for a single logical date."""
    landing_dir.mkdir(parents=True, exist_ok=True)
    definition = scenario(plan.scenario_id)
    data_date = stale_data_date(plan.logical_date, plan.scenario_id)
    rng = random.Random(plan.seed)

    baseline = volume * _WEEKDAY_FACTOR[data_date.weekday()] * definition.volume_factor
    orders = max(10, int(baseline * (1.0 + rng.gauss(0.0, 0.04))))

    columns = SCHEMA_CHANGE_COLUMNS if definition.schema_change else CONTRACT_COLUMNS
    rows: list[dict[str, Any]] = []
    customers = customer_ids()

    for index in range(orders):
        customer_id: str | None = rng.choice(customers)
        if rng.random() < definition.null_rate:
            customer_id = None
        _, base_price = rng.choice(_PRODUCTS)
        unit_price = round(base_price * rng.uniform(0.6, 1.4) * definition.price_factor, 2)
        quantity = rng.randint(1, 4)
        discount = rng.choice((0.0, 0.0, 0.0, 0.05, 0.10, 0.15))
        order_total = round(unit_price * quantity * (1.0 - discount), 2)
        status = rng.choices(
            ("completed", "cancelled", "pending", "unknown"),
            weights=(0.92, 0.05, 0.027, 0.003),
            k=1,
        )[0]
        payment = rng.choices(
            ("credit_card", "pix", "boleto", "debit_card", "cash?"),
            weights=(0.45, 0.35, 0.12, 0.078, 0.002),
            k=1,
        )[0]
        channel = rng.choice(("web", "app", "store"))
        order_ts = datetime.combine(data_date, datetime.min.time()) + timedelta(
            hours=rng.randint(0, 23), minutes=rng.randint(0, 59)
        )
        row = {
            "order_id": f"O{data_date:%Y%m%d}-{index:06d}",
            "customer_id": customer_id,
            "order_ts": order_ts.isoformat(timespec="seconds"),
            "order_date": data_date.isoformat(),
            "status": status,
            "payment_method": payment,
            "channel": channel,
            "unit_price": unit_price,
            "quantity": quantity,
            "discount": discount,
            "order_total": order_total,
        }
        if definition.schema_change:
            row["discount_pct"] = row.pop("discount")
        rows.append(row)

    duplicates = int(len(rows) * definition.duplicate_rate)
    for _ in range(duplicates):
        source = rng.choice(rows)
        duplicate = dict(source)
        duplicate["order_ts"] = datetime.fromisoformat(str(source["order_ts"])).isoformat(
            timespec="seconds"
        )
        rows.append(duplicate)

    path = landing_dir / f"orders_{plan.logical_date.isoformat()}.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row[column] for column in columns})

    manifest = read_manifest(landing_dir)
    manifest[plan.logical_date.isoformat()] = {
        "scenario_id": plan.scenario_id,
        "seed": plan.seed,
        "volume": volume,
        "data_date": data_date.isoformat(),
        "rows": len(rows),
    }
    _write_manifest(landing_dir, manifest)
    return path


def generate_plan(landing_dir: Path, plans: Iterable[DayPlan], *, volume: int) -> list[Path]:
    return [generate_day(landing_dir, plan, volume=volume) for plan in plans]


def read_header(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        return next(reader, [])


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_raw(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read header and rows in one pass (still small enough for the demo)."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def order_dates(rows: Sequence[dict[str, str]]) -> set[date]:
    return {date.fromisoformat(row["order_date"]) for row in rows}


__all__ = [
    "CONTRACT_COLUMNS",
    "MANIFEST_NAME",
    "SCHEMA_CHANGE_COLUMNS",
    "customer_ids",
    "generate_day",
    "generate_plan",
    "order_dates",
    "read_header",
    "read_manifest",
    "read_raw",
    "read_rows",
]
