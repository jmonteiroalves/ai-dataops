"""Synthetic data determinism and scenario effects."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ai_dataops.config import Settings
from ai_dataops.pipeline.scenarios import DayPlan, build_plan
from ai_dataops.pipeline.synthetic import (
    CONTRACT_COLUMNS,
    generate_day,
    read_manifest,
    read_raw,
)

DAY = date(2026, 11, 17)


def _generate(
    settings: Settings, scenario: str, volume: int = 300
) -> tuple[list[str], list[dict[str, str]]]:
    generate_day(settings.landing_dir, DayPlan(DAY, scenario, 123), volume=volume)
    path = settings.landing_dir / f"orders_{DAY.isoformat()}.csv"
    return read_raw(path)


def test_generation_is_deterministic(demo_settings: Settings) -> None:
    _generate(demo_settings, "normal")
    first = (demo_settings.landing_dir / f"orders_{DAY.isoformat()}.csv").read_bytes()
    _generate(demo_settings, "normal")
    second = (demo_settings.landing_dir / f"orders_{DAY.isoformat()}.csv").read_bytes()

    assert first == second


def test_volume_drop_reduces_rows(demo_settings: Settings) -> None:
    header, normal = _generate(demo_settings, "normal", volume=1000)
    _, dropped = _generate(demo_settings, "volume_drop", volume=1000)

    assert header == list(CONTRACT_COLUMNS)
    assert len(dropped) < len(normal) * 0.75


def test_null_spike_increases_nulls(demo_settings: Settings) -> None:
    _, normal = _generate(demo_settings, "normal", volume=600)
    _, spike = _generate(demo_settings, "null_spike", volume=600)

    normal_nulls = sum(1 for row in normal if not row["customer_id"])
    spike_nulls = sum(1 for row in spike if not row["customer_id"])

    assert normal_nulls / len(normal) < 0.02
    assert spike_nulls / len(spike) > 0.04


def test_schema_change_renames_discount(demo_settings: Settings) -> None:
    header, rows = _generate(demo_settings, "schema_change")

    assert "discount" not in header
    assert "discount_pct" in header
    assert "discount_pct" in rows[0]


def test_manifest_records_scenario_and_data_date(demo_settings: Settings) -> None:
    _generate(demo_settings, "stale_data")
    entry = read_manifest(demo_settings.landing_dir)[DAY.isoformat()]

    assert entry["scenario_id"] == "stale_data"
    assert entry["data_date"] == "2026-11-15"


def test_plan_injects_the_demo_story() -> None:
    plan = build_plan(date(2026, 11, 18), 45)
    scenarios = {item.scenario_id for item in plan}

    assert "volume_drop" in scenarios
    assert "schema_change" in scenarios
    assert sum(1 for item in plan if item.scenario_id != "normal") == 16


def test_plan_can_disable_injection() -> None:
    plan = build_plan(date(2026, 11, 18), 10, inject=False)

    assert {item.scenario_id for item in plan} == {"normal"}
    assert len(plan) == 10


def test_output_path_is_inside_the_landing_directory(demo_settings: Settings) -> None:
    path = generate_day(demo_settings.landing_dir, DayPlan(DAY, "normal", 1), volume=10)

    assert isinstance(path, Path)
    assert path.parent == demo_settings.landing_dir
