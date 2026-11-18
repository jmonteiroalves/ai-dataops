"""Mark abandoned executions as failed.

A process killed mid-run leaves a ``running`` row. The reaper is idempotent and
is safe to run on every schedule.
"""

from __future__ import annotations

from datetime import timedelta

from ai_dataops.config import Settings
from ai_dataops.domain.ports import Warehouse
from ai_dataops.timeutil import utcnow


def reap_stale_runs(
    warehouse: Warehouse,
    settings: Settings,
    *,
    older_than_minutes: int = 180,
) -> int:
    cutoff = utcnow() - timedelta(minutes=older_than_minutes)
    stale = warehouse.query(
        f"""
        SELECT execution_id, started_at
        FROM {settings.bq_obs_dataset}.pipeline_executions
        WHERE status = 'running' AND started_at < ?
        """,
        (cutoff,),
    )
    for row in stale:
        finished = utcnow()
        elapsed = (finished - row["started_at"]).total_seconds()
        warehouse.execute(
            f"""
            UPDATE {settings.bq_obs_dataset}.pipeline_executions
            SET status = 'failed', finished_at = ?, duration_seconds = ?,
                error_code = 'STALE_RUN',
                error_message = 'execution exceeded the allowed runtime and was reaped'
            WHERE execution_id = ?
            """,
            (finished, elapsed, row["execution_id"]),
        )
    return len(stale)


__all__ = ["reap_stale_runs"]
