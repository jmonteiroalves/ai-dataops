"""Tests for typed settings and environment resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_dataops.config import Engine, Environment, LogFormat, load_settings
from ai_dataops.errors import ConfigurationError


def test_defaults_are_local_first(isolated_env: Path) -> None:
    settings = load_settings()

    assert settings.env is Environment.LOCAL
    assert settings.engine is Engine.DUCKDB
    assert settings.project_id is None
    assert settings.region == "us-central1"
    assert settings.log_format is LogFormat.JSON
    assert settings.timezone == "America/Sao_Paulo"
    assert settings.max_bytes_billed_bytes == 200 * 1024 * 1024
    assert settings.duckdb_path == Path("data/local/dataops.duckdb")
    assert settings.demo_dataset == "demo"
    assert settings.rules_dir == Path("rules")
    assert settings.landing_dir == Path("data/landing")


def test_environment_variables_override_defaults(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATAOPS_ENV", "gcp")
    monkeypatch.setenv("DATAOPS_ENGINE", "BIGQUERY")
    monkeypatch.setenv("DATAOPS_PROJECT_ID", "demo-project")
    monkeypatch.setenv("DATAOPS_LOG_FORMAT", "CONSOLE")
    monkeypatch.setenv("DATAOPS_LOG_LEVEL", "debug")
    monkeypatch.setenv("DATAOPS_MAX_BYTES_BILLED_BYTES", "1024")

    settings = load_settings()

    assert settings.env is Environment.GCP
    assert settings.engine is Engine.BIGQUERY
    assert settings.project_id == "demo-project"
    assert settings.log_format is LogFormat.CONSOLE
    assert settings.log_level == "DEBUG"
    assert settings.max_bytes_billed_bytes == 1024


def test_blank_project_id_becomes_none(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATAOPS_PROJECT_ID", "   ")

    assert load_settings().project_id is None


def test_bigquery_requires_a_project_id(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATAOPS_ENGINE", "bigquery")

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()

    assert "DATAOPS_PROJECT_ID" in str(excinfo.value)
    fields = [error["field"] for error in excinfo.value.context["errors"]]
    assert any("project_id" in field or "__root__" in field for field in fields)


def test_unknown_engine_is_rejected(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATAOPS_ENGINE", "snowflake")

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()

    assert excinfo.value.context["errors"][0]["field"] == "engine"


def test_invalid_region_is_rejected(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATAOPS_REGION", "us central")

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()

    assert "region" in str(excinfo.value.context["errors"][0]["field"])


def test_invalid_timezone_is_rejected(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATAOPS_TIMEZONE", "Mars/Olympus")

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()

    assert "timezone" in str(excinfo.value.context["errors"][0]["field"])


def test_settings_are_immutable(isolated_env: Path) -> None:
    settings = load_settings()

    with pytest.raises(ValidationError):
        settings.region = "europe-west1"  # type: ignore[misc]


def test_public_dict_is_json_safe(isolated_env: Path) -> None:
    payload = load_settings().public_dict()

    assert payload["engine"] == "duckdb"
    assert payload["env"] == "local"
    assert payload["duckdb_path"].endswith("dataops.duckdb")
    assert all(isinstance(value, (str, int, float, bool, type(None))) for value in payload.values())


def test_tzinfo_property_resolves_the_business_timezone(isolated_env: Path) -> None:
    settings = load_settings()

    assert settings.tzinfo.key == "America/Sao_Paulo"
