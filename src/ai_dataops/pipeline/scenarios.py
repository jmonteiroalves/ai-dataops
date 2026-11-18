"""Reproducible demo scenarios.

Each scenario is a deterministic perturbation of an otherwise stable sales
stream. The scenario id is persisted with the execution, which turns the demo
into a labelled dataset for the detector backtest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ai_dataops.timeutil import date_range


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    name: str
    description: str
    volume_factor: float = 1.0
    duplicate_rate: float = 0.0
    null_rate: float = 0.004
    price_factor: float = 1.0
    simulated_delay_seconds: float = 0.0
    stale_days: int = 0
    schema_change: bool = False
    fail_after_fraction: float | None = None
    expected_family: str | None = None


SCENARIOS: dict[str, Scenario] = {
    "normal": Scenario(
        scenario_id="normal",
        name="Normal execution",
        description="Stable volume with weekly seasonality and low rejection rate.",
    ),
    "ingestion_failure": Scenario(
        scenario_id="ingestion_failure",
        name="Ingestion failure",
        description="Upstream extract times out after half of the file is read.",
        fail_after_fraction=0.5,
        expected_family="failure",
    ),
    "volume_drop": Scenario(
        scenario_id="volume_drop",
        name="Volume drop",
        description="About 45% fewer orders than the weekday baseline.",
        volume_factor=0.55,
        expected_family="volume",
    ),
    "duplicate_spike": Scenario(
        scenario_id="duplicate_spike",
        name="Duplicate spike",
        description="Duplicate order_id rate rises from 0% to 3.5%.",
        duplicate_rate=0.035,
        expected_family="duplicates",
    ),
    "null_spike": Scenario(
        scenario_id="null_spike",
        name="Null spike",
        description="customer_id null rate rises from 0.4% to 6.2%.",
        null_rate=0.062,
        expected_family="nulls",
    ),
    "price_anomaly": Scenario(
        scenario_id="price_anomaly",
        name="Price anomaly",
        description="Unit prices shift ~80% up, changing the order_total distribution.",
        price_factor=1.8,
        expected_family="price",
    ),
    "slow_run": Scenario(
        scenario_id="slow_run",
        name="Slow run",
        description="A scenario-injected delay makes the run 4-6x slower.",
        simulated_delay_seconds=4.0,
        expected_family="duration",
    ),
    "stale_data": Scenario(
        scenario_id="stale_data",
        name="Stale data",
        description="The run succeeds but produces data for two days earlier.",
        stale_days=2,
        expected_family="freshness",
    ),
    "schema_change": Scenario(
        scenario_id="schema_change",
        name="Schema change",
        description="Landing file renames discount to discount_pct, breaking the contract.",
        schema_change=True,
        expected_family="schema",
    ),
}

# Offsets (in days before the end date) used when injecting the demo story.
# Three cycles give 16 labelled incidents in a 45-day window and 24 in 90 days.
_STORY = (
    "ingestion_failure",
    "schema_change",
    "stale_data",
    "slow_run",
    "price_anomaly",
    "duplicate_spike",
    "null_spike",
    "volume_drop",
)
_INJECTIONS: dict[int, str] = {
    start + 2 * index: scenario_id
    for start in (6, 30, 54)
    for index, scenario_id in enumerate(_STORY)
}


@dataclass(frozen=True, slots=True)
class DayPlan:
    logical_date: date
    scenario_id: str
    seed: int


def build_plan(end_date: date, days: int, *, inject: bool = True, seed: int = 42) -> list[DayPlan]:
    """Build a chronological plan, optionally with the scenario story injected."""
    plan: list[DayPlan] = []
    for day in date_range(end_date, days):
        offset = (end_date - day).days
        scenario_id = _INJECTIONS.get(offset, "normal") if inject else "normal"
        plan.append(DayPlan(day, scenario_id, seed + day.toordinal()))
    return plan


def scenario(scenario_id: str) -> Scenario:
    try:
        return SCENARIOS[scenario_id]
    except KeyError as exc:
        raise ValueError(f"unknown scenario: {scenario_id}") from exc


def scenario_rows() -> list[dict[str, object]]:
    return [
        {
            "scenario_id": item.scenario_id,
            "name": item.name,
            "description": item.description,
            "config_json": {
                "volume_factor": item.volume_factor,
                "duplicate_rate": item.duplicate_rate,
                "null_rate": item.null_rate,
                "price_factor": item.price_factor,
                "simulated_delay_seconds": item.simulated_delay_seconds,
                "stale_days": item.stale_days,
                "schema_change": item.schema_change,
                "fail_after_fraction": item.fail_after_fraction,
            },
            "expected_signal": item.expected_family,
            "is_active": True,
        }
        for item in SCENARIOS.values()
    ]


def stale_data_date(logical_date: date, scenario_id: str) -> date:
    return logical_date - timedelta(days=scenario(scenario_id).stale_days)


__all__ = [
    "SCENARIOS",
    "DayPlan",
    "Scenario",
    "build_plan",
    "scenario",
    "scenario_rows",
    "stale_data_date",
]
