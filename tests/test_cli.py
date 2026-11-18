"""Tests for the CLI contract (exit codes and machine-readable output)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ai_dataops.cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_version_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.startswith("ai-dataops ")


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    assert capsys.readouterr().out.startswith("ai-dataops ")


def test_config_prints_resolved_settings(
    isolated_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["config"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["engine"] == "duckdb"
    assert payload["env"] == "local"
    assert payload["region"] == "us-central1"


def test_config_supports_compact_output(
    isolated_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["config", "--no-indent"]) == 0

    output = capsys.readouterr().out.strip()
    assert output.startswith("{") and output.endswith("}")
    assert len(output.splitlines()) == 1


def test_check_reports_ready_and_logs_to_stderr(
    isolated_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check"]) == 0

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary == {
        "status": "ok",
        "env": "local",
        "engine": "duckdb",
        "region": "us-central1",
        "timezone": "America/Sao_Paulo",
    }
    log_line = json.loads(captured.err.strip())
    assert log_line["severity"] == "INFO"
    assert log_line["message"] == "configuration valid"
    assert log_line["engine"] == "duckdb"


def test_invalid_configuration_returns_infrastructure_exit_code(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATAOPS_ENGINE", "bigquery")

    assert main(["check"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "configuration_error" in captured.err
    assert "DATAOPS_PROJECT_ID" in captured.err


def test_missing_command_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])

    assert excinfo.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_module_entry_point_runs_the_cli() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ai_dataops", "version"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.startswith("ai-dataops ")


def _configure_demo_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATAOPS_DUCKDB_PATH", str(tmp_path / "dataops.duckdb"))
    monkeypatch.setenv("DATAOPS_LANDING_DIR", str(tmp_path / "landing"))
    monkeypatch.setenv("DATAOPS_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("DATAOPS_SERVING_DIR", str(tmp_path / "serving"))
    monkeypatch.setenv("DATAOPS_RULES_DIR", str(REPO_ROOT / "rules"))
    monkeypatch.setenv("DATAOPS_PROMPTS_DIR", str(REPO_ROOT / "prompts"))


def test_generate_and_run_daily(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure_demo_env(monkeypatch, isolated_env)

    assert main(["generate", "--days", "1", "--end-date", "2026-11-18", "--volume", "40"]) == 0
    generated = json.loads(capsys.readouterr().out)
    assert generated["generated"] == 1
    assert (isolated_env / "landing" / "orders_2026-11-18.csv").exists()

    assert main(["run-daily", "--date", "2026-11-18"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "succeeded"
    assert summary["records_processed"] > 0
    assert summary["rules"]["total"] == 11


def test_status_command(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure_demo_env(monkeypatch, isolated_env)
    main(["generate", "--days", "1", "--end-date", "2026-11-18", "--volume", "20"])
    main(["run-daily", "--date", "2026-11-18"])
    capsys.readouterr()

    assert main(["status", "--date", "2026-11-18"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["execution"]["status"] == "succeeded"
    assert "rules" in status


def test_backtest_command_on_a_single_day(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure_demo_env(monkeypatch, isolated_env)
    main(["generate", "--days", "1", "--end-date", "2026-11-18", "--volume", "20"])
    main(["run-daily", "--date", "2026-11-18"])
    capsys.readouterr()

    assert main(["backtest", "--days", "1", "--end-date", "2026-11-18", "--warmup-days", "0"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["evaluated_days"] == 1


def test_stage_commands_and_reaper(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure_demo_env(monkeypatch, isolated_env)
    main(["generate", "--days", "2", "--end-date", "2026-11-18", "--volume", "30"])
    capsys.readouterr()

    for command in (
        ["bootstrap"],
        ["run-pipeline", "--date", "2026-11-18"],
        ["run-quality", "--date", "2026-11-18"],
        ["detect", "--date", "2026-11-18"],
        ["correlate", "--date", "2026-11-18"],
        ["status", "--date", "2026-11-18"],
        ["reap"],
    ):
        assert main(command) == 0, command
        capsys.readouterr()


def test_demo_command_runs_backfill_and_backtest(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure_demo_env(monkeypatch, isolated_env)

    assert (
        main(
            [
                "demo",
                "--days",
                "2",
                "--end-date",
                "2026-11-18",
                "--volume",
                "20",
                "--warmup-days",
                "0",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert '"days": 2' in output
    assert '"recall"' in output


def test_pipeline_failure_returns_a_business_exit_code(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure_demo_env(monkeypatch, isolated_env)
    main(["generate", "--days", "9", "--end-date", "2026-11-18", "--volume", "20"])
    capsys.readouterr()

    assert main(["run-pipeline", "--date", "2026-11-10"]) == 1
    captured = capsys.readouterr()
    assert "schema_error" in captured.err


def test_diagnose_and_export_commands(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure_demo_env(monkeypatch, isolated_env)
    main(["generate", "--days", "9", "--end-date", "2026-11-18", "--volume", "20"])
    capsys.readouterr()

    assert main(["run-daily", "--date", "2026-11-10"]) == 1
    capsys.readouterr()

    assert main(["diagnose", "--date", "2026-11-10", "--provider", "fake"]) == 0
    diagnosis = json.loads(capsys.readouterr().out)
    assert diagnosis["results"][0]["status"] == "success"
    assert diagnosis["results"][0]["top_hypothesis"]

    assert main(["export-serving", "--date", "2026-11-18"]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert Path(exported["snapshot"]).exists()


def test_run_daily_generates_missing_landing_data(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure_demo_env(monkeypatch, isolated_env)
    monkeypatch.setenv("DATAOPS_DEMO_DAILY_VOLUME", "30")

    assert main(["run-daily", "--date", "2026-11-18"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "succeeded"
    assert summary["records_processed"] > 0
    assert (isolated_env / "landing" / "orders_2026-11-18.csv").exists()
