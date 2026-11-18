"""Stale execution reaper."""

from __future__ import annotations

from datetime import date, timedelta

from ai_dataops.adapters.duckdb import DuckDBWarehouse
from ai_dataops.config import Settings
from ai_dataops.instrumentation.reaper import reap_stale_runs
from ai_dataops.timeutil import utcnow


def test_reap_marks_abandoned_runs_failed(
    warehouse: DuckDBWarehouse, demo_settings: Settings
) -> None:
    started = utcnow() - timedelta(hours=6)
    warehouse.write_rows(
        f"{demo_settings.bq_obs_dataset}.pipeline_executions",
        [
            {
                "execution_id": "abandoned",
                "pipeline_id": "sales_daily",
                "logical_date": date(2026, 11, 18),
                "attempt": 1,
                "scenario_id": None,
                "status": "running",
                "started_at": started,
                "finished_at": None,
                "duration_seconds": None,
                "records_read": 0,
                "records_processed": 0,
                "records_rejected": 0,
                "rejection_rate": 0.0,
                "error_code": None,
                "error_message": None,
                "triggered_by": "manual",
                "execution_metadata": "{}",
                "created_at": started,
            }
        ],
    )

    assert reap_stale_runs(warehouse, demo_settings, older_than_minutes=60) == 1
    row = warehouse.query(
        f"SELECT status, error_code FROM {demo_settings.bq_obs_dataset}.pipeline_executions "
        "WHERE execution_id = 'abandoned'"
    )[0]
    assert row["status"] == "failed"
    assert row["error_code"] == "STALE_RUN"
    assert reap_stale_runs(warehouse, demo_settings, older_than_minutes=60) == 0
