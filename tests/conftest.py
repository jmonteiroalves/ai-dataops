"""Shared fixtures: a throwaway DuckDB warehouse with seeded metadata."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from ai_dataops.adapters.duckdb import DuckDBWarehouse
from ai_dataops.adapters.schema import initialize_schema
from ai_dataops.bootstrap import bootstrap
from ai_dataops.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run a test with no DATAOPS_* variables and no project ``.env`` in scope."""
    for key in list(__import__("os").environ):
        if key.startswith("DATAOPS_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def demo_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        duckdb_path=tmp_path / "dataops.duckdb",
        landing_dir=tmp_path / "landing",
        rules_dir=REPO_ROOT / "rules",
        prompts_dir=REPO_ROOT / "prompts",
        reports_dir=tmp_path / "reports",
        serving_dir=tmp_path / "serving",
        demo_dataset="demo",
    )


@pytest.fixture
def warehouse(demo_settings: Settings) -> Iterator[DuckDBWarehouse]:
    instance = DuckDBWarehouse(demo_settings.duckdb_path)
    initialize_schema(instance, demo_settings)
    bootstrap(instance, demo_settings)
    try:
        yield instance
    finally:
        instance.close()
