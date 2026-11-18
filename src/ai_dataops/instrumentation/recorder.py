"""Execution recorder.

Guarantees that every run has a terminal status, deterministic id and the
counters the rest of the platform relies on. Metrics are emitted through the
same object so that a metric can always be traced to an execution.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from types import TracebackType
from typing import Any, Literal

from ai_dataops.domain.ports import Warehouse
from ai_dataops.errors import DataOpsError
from ai_dataops.ids import deterministic_id
from ai_dataops.timeutil import at_noon, utcnow


class ExecutionRecorder:
    """Context manager that records a pipeline execution and its steps."""

    def __init__(
        self,
        warehouse: Warehouse,
        pipeline_id: str,
        logical_date: date,
        *,
        obs_dataset: str,
        attempt: int = 1,
        scenario_id: str | None = None,
        triggered_by: str = "manual",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.warehouse = warehouse
        self.pipeline_id = pipeline_id
        self.logical_date = logical_date
        self.obs_dataset = obs_dataset
        self.attempt = attempt
        self.scenario_id = scenario_id
        self.triggered_by = triggered_by
        self.metadata = dict(metadata or {})
        self.execution_id = deterministic_id("execution", pipeline_id, logical_date, attempt)
        self.started_at: datetime = utcnow()
        self.finished_at: datetime | None = None
        self.status = "running"
        self.records_read = 0
        self.records_processed = 0
        self.records_rejected = 0
        self.error_code: str | None = None
        self.error_message: str | None = None
        self.simulated_delay_seconds = float(self.metadata.get("simulated_delay_seconds", 0.0))
        self._finished = False

    # ------------------------------------------------------------------ events
    def __enter__(self) -> ExecutionRecorder:
        self._write_execution()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        if exc is not None and not self._finished:
            code = exc.code if isinstance(exc, DataOpsError) else "UNEXPECTED_ERROR"
            self.finish("failed", error_code=str(code), error_message=str(exc)[:2000])
        elif not self._finished:
            self.finish("succeeded")
        return False

    def set_counters(self, *, read: int, processed: int, rejected: int) -> None:
        self.records_read = read
        self.records_processed = processed
        self.records_rejected = rejected

    def start_step(self, name: str) -> datetime:
        del name
        return utcnow()

    def finish_step(self, name: str, started_at: datetime, *, status: str = "succeeded") -> None:
        finished = utcnow()
        self.warehouse.write_rows(
            f"{self.obs_dataset}.execution_steps",
            [
                {
                    "execution_id": self.execution_id,
                    "step_name": name,
                    "status": status,
                    "started_at": started_at,
                    "finished_at": finished,
                    "duration_ms": int((finished - started_at).total_seconds() * 1000),
                    "details": {},
                }
            ],
        )

    def log_event(self, level: str, message: str, *, context: dict[str, Any] | None = None) -> None:
        event_at = utcnow()
        self.warehouse.write_rows(
            f"{self.obs_dataset}.execution_log_events",
            [
                {
                    "event_id": deterministic_id("event", self.execution_id, event_at.isoformat()),
                    "execution_id": self.execution_id,
                    "event_at": event_at,
                    "level": level,
                    "message": message[:1000],
                    "context": context or {},
                }
            ],
        )

    def record_metric(
        self,
        entity_type: str,
        entity_id: str,
        metric_name: str,
        value: float,
        unit: str,
        *,
        dimensions: dict[str, Any] | None = None,
        observed_at: datetime | None = None,
    ) -> None:
        observation_id = deterministic_id(
            "metric", self.execution_id, entity_type, entity_id, metric_name
        )
        self.warehouse.write_rows(
            f"{self.obs_dataset}.metric_observations",
            [
                {
                    "observation_id": observation_id,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "metric_name": metric_name,
                    "value": float(value),
                    "unit": unit,
                    "execution_id": self.execution_id,
                    "observed_at": observed_at or at_noon(self.logical_date),
                    "dimensions": dimensions or {},
                    "is_valid": True,
                }
            ],
        )

    def record_schema(
        self,
        dataset_key: str,
        columns: list[tuple[str, str, bool]],
        *,
        execution_id: str | None = None,
    ) -> str:
        captured_at = utcnow()
        schema_hash = hashlib.sha256(
            "|".join(f"{name}:{kind}:{nullable}" for name, kind, nullable in columns).encode()
        ).hexdigest()
        rows = [
            {
                "snapshot_id": deterministic_id(
                    "schema", dataset_key, execution_id or self.execution_id, name
                ),
                "dataset_key": dataset_key,
                "execution_id": execution_id or self.execution_id,
                "captured_at": captured_at,
                "column_name": name,
                "data_type": kind,
                "is_nullable": nullable,
                "ordinal_position": index + 1,
                "schema_hash": schema_hash,
            }
            for index, (name, kind, nullable) in enumerate(columns)
        ]
        self.warehouse.write_rows(f"{self.obs_dataset}.schema_snapshots", rows)
        return schema_hash

    def finish(
        self,
        status: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if self._finished:
            return
        self.finished_at = utcnow()
        self.status = status
        self.error_code = error_code
        self.error_message = error_message
        measured = (self.finished_at - self.started_at).total_seconds()
        self.metadata["measured_duration_seconds"] = round(measured, 3)
        self._finished = True
        self._write_execution()
        self.record_metric(
            "pipeline",
            self.pipeline_id,
            "duration_seconds",
            round(measured + self.simulated_delay_seconds, 3),
            "seconds",
        )
        if status == "failed":
            self.log_event(
                "ERROR", error_message or "execution failed", context={"code": error_code}
            )

    # ----------------------------------------------------------------- writers
    def _write_execution(self) -> None:
        finished = self.finished_at
        duration = (
            round((finished - self.started_at).total_seconds() + self.simulated_delay_seconds, 3)
            if finished is not None
            else None
        )
        rejection_rate = self.records_rejected / self.records_read if self.records_read else 0.0
        self.warehouse.write_rows(
            f"{self.obs_dataset}.pipeline_executions",
            [
                {
                    "execution_id": self.execution_id,
                    "pipeline_id": self.pipeline_id,
                    "logical_date": self.logical_date,
                    "attempt": self.attempt,
                    "scenario_id": self.scenario_id,
                    "status": self.status,
                    "started_at": self.started_at,
                    "finished_at": finished,
                    "duration_seconds": duration,
                    "records_read": self.records_read,
                    "records_processed": self.records_processed,
                    "records_rejected": self.records_rejected,
                    "rejection_rate": round(rejection_rate, 6),
                    "error_code": self.error_code,
                    "error_message": self.error_message,
                    "triggered_by": self.triggered_by,
                    "execution_metadata": json.dumps(self.metadata, default=str),
                    "created_at": self.started_at,
                }
            ],
        )


__all__ = ["ExecutionRecorder"]
