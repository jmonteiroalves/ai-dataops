"""Centralised, typed configuration.

Only this module reads the environment. Everything else receives ``Settings``
or a narrow slice of it, which keeps adapters and business logic free of
``os.environ`` access (see AGENTS.md).
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_core import ValidationError as PydanticValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai_dataops.errors import ConfigurationError

_REGION_PATTERN = re.compile(r"^[a-z]{2,}(-[a-z]+)+[0-9]+$")

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Environment(StrEnum):
    """Where the process runs."""

    LOCAL = "local"
    GCP = "gcp"


class Engine(StrEnum):
    """Warehouse adapter selection (ADR-001)."""

    DUCKDB = "duckdb"
    BIGQUERY = "bigquery"


class LogFormat(StrEnum):
    """Structured output for machine consumption, console for humans."""

    JSON = "json"
    CONSOLE = "console"


class Settings(BaseSettings):
    """Resolved application settings.

    Values come from ``DATAOPS_*`` environment variables and an optional ``.env``
    file in the current directory. The object is immutable so that configuration
    cannot drift mid-run.
    """

    model_config = SettingsConfigDict(
        env_prefix="DATAOPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    env: Environment = Environment.LOCAL
    engine: Engine = Engine.DUCKDB

    project_id: str | None = None
    region: str = "us-central1"

    bq_meta_dataset: str = "dataops_meta"
    bq_obs_dataset: str = "dataops_obs"
    bq_quality_dataset: str = "dataops_quality"
    bq_detect_dataset: str = "dataops_detect"
    bq_ai_dataset: str = "dataops_ai"
    bq_serving_dataset: str = "dataops_serving"

    duckdb_path: Path = Path("data/local/dataops.duckdb")
    demo_dataset: str = "demo"
    landing_dir: Path = Path("data/landing")
    rules_dir: Path = Path("rules")
    reports_dir: Path = Path("data/reports")
    serving_dir: Path = Path("data/serving")
    snapshot_bucket: str | None = None
    prompts_dir: Path = Path("prompts")

    demo_daily_volume: int = 1500
    diagnosis_enabled: bool = True
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_location: str = "global"
    diagnosis_timeout_seconds: int = 30
    diagnosis_budget_usd: float = 2.0
    diagnosis_min_severity: str = "medium"
    diagnosis_max_per_run: int = 5

    log_level: LogLevel = "INFO"
    log_format: LogFormat = LogFormat.JSON

    timezone: str = "America/Sao_Paulo"

    max_bytes_billed_bytes: int = Field(default=200 * 1024 * 1024, gt=0)

    @field_validator("env", "engine", "log_format", mode="before")
    @classmethod
    def _normalise_enum(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("project_id", mode="before")
    @classmethod
    def _blank_project_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("region")
    @classmethod
    def _validate_region(cls, value: str) -> str:
        if not _REGION_PATTERN.fullmatch(value):
            raise ValueError(f"invalid GCP region: {value!r}")
        return value

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {value!r}") from exc
        return value

    @model_validator(mode="after")
    def _require_project_for_bigquery(self) -> Self:
        if self.engine is Engine.BIGQUERY and self.project_id is None:
            raise ValueError("DATAOPS_PROJECT_ID is required when DATAOPS_ENGINE=bigquery")
        return self

    @property
    def tzinfo(self) -> ZoneInfo:
        """Business timezone used to derive logical dates."""
        return ZoneInfo(self.timezone)

    def public_dict(self) -> dict[str, Any]:
        """JSON-safe view of the settings, safe to print in logs and CLI output."""
        return self.model_dump(mode="json")


def load_settings() -> Settings:
    """Load settings, translating validation failures into a domain error."""
    try:
        return Settings()
    except PydanticValidationError as exc:
        details = [
            {
                "field": ".".join(str(part) for part in error["loc"]) or "__root__",
                "message": error["msg"],
            }
            for error in exc.errors(include_url=False)
        ]
        summary = "; ".join(f"{item['field']}: {item['message']}" for item in details)
        raise ConfigurationError(
            f"Invalid configuration ({summary})", context={"errors": details}
        ) from exc


__all__ = [
    "Engine",
    "Environment",
    "LogFormat",
    "LogLevel",
    "Settings",
    "load_settings",
]
