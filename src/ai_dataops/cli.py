"""Command-line entry point and composition root.

Subcommands are the same code path used locally and by the Cloud Run Job:
exit code ``1`` for data/business failures, ``2`` for infrastructure errors.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from ai_dataops import __version__
from ai_dataops.adapters.duckdb import DuckDBWarehouse
from ai_dataops.adapters.gcs import upload_text
from ai_dataops.adapters.schema import initialize_schema
from ai_dataops.anomaly.backtest import run_backtest
from ai_dataops.anomaly.correlation import correlate
from ai_dataops.anomaly.engine import run_detection
from ai_dataops.bootstrap import bootstrap
from ai_dataops.config import Engine, Settings, load_settings
from ai_dataops.diagnosis.engine import diagnose_incident, pending_incidents
from ai_dataops.diagnosis.evaluation import evaluate_diagnoses
from ai_dataops.diagnosis.provider import DiagnosisProvider, FakeDiagnosisProvider
from ai_dataops.domain.models import RuleDefinition
from ai_dataops.domain.ports import Warehouse
from ai_dataops.errors import DataOpsError, NotFoundError
from ai_dataops.instrumentation.reaper import reap_stale_runs
from ai_dataops.logging import configure_logging, get_logger
from ai_dataops.pipeline.sales import PIPELINE_ID, run_sales_pipeline
from ai_dataops.pipeline.scenarios import DayPlan, build_plan
from ai_dataops.pipeline.synthetic import generate_day, generate_plan
from ai_dataops.quality.catalog import load_rules, sync_rules
from ai_dataops.quality.engine import run_quality
from ai_dataops.serving.export import export_snapshot
from ai_dataops.timeutil import business_today, parse_date

logger = get_logger("cli")


# --------------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dataops",
        description="AI DataOps: pipeline observability, data quality and incident detection.",
    )
    parser.add_argument("--version", action="version", version=f"ai-dataops {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    subparsers.add_parser("version", help="print the installed version")

    config_parser = subparsers.add_parser("config", help="print the resolved configuration")
    config_parser.add_argument(
        "--no-indent", action="store_true", help="emit compact JSON on a single line"
    )

    subparsers.add_parser("check", help="validate configuration and report readiness")
    subparsers.add_parser("bootstrap", help="create the schema and seed metadata")

    generate = subparsers.add_parser("generate", help="write seeded landing files")
    _add_window_arguments(generate)
    generate.add_argument("--no-inject", action="store_true", help="do not inject scenarios")

    for name, help_text in (
        ("run-pipeline", "run the sales pipeline for one date"),
        ("run-quality", "evaluate quality rules for one date"),
        ("detect", "detect anomalies for one date"),
        ("correlate", "correlate signals into an incident for one date"),
        ("status", "print the observability summary for one date"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        _add_date_argument(command)
        if name == "run-pipeline":
            command.add_argument("--scenario", default=None, help="scenario id override")

    daily = subparsers.add_parser("run-daily", help="pipeline + quality + detection + correlation")
    _add_date_argument(daily)
    daily.add_argument("--scenario", default=None, help="scenario id override")

    backfill = subparsers.add_parser("backfill", help="generate and run a labelled history")
    _add_window_arguments(backfill)
    backfill.add_argument("--no-inject", action="store_true", help="do not inject scenarios")

    backtest = subparsers.add_parser("backtest", help="score detectors against scenario labels")
    _add_window_arguments(backtest)
    backtest.add_argument(
        "--warmup-days", type=int, default=14, help="days excluded for detector warm-up"
    )

    demo = subparsers.add_parser("demo", help="generate, backfill and backtest a short demo")
    _add_window_arguments(demo)
    demo.add_argument("--no-inject", action="store_true", help="do not inject scenarios")
    demo.add_argument(
        "--warmup-days", type=int, default=14, help="days excluded for detector warm-up"
    )

    diagnose = subparsers.add_parser("diagnose", help="generate AI diagnoses")
    _add_date_argument(diagnose)
    diagnose.add_argument("--incident", default=None, help="specific incident id")
    diagnose.add_argument("--provider", choices=["vertex", "fake"], default="vertex")
    diagnose.add_argument("--force", action="store_true", help="ignore the dedup cache")
    diagnose.add_argument(
        "--limit", type=int, default=None, help="maximum pending incidents to process"
    )

    evaluation = subparsers.add_parser(
        "evaluate-diagnosis", help="score diagnoses against scenario labels"
    )
    evaluation.add_argument("--provider", choices=["vertex", "fake"], default="vertex")
    evaluation.add_argument("--limit", type=int, default=24)
    evaluation.add_argument("--force", action="store_true", help="re-run even if cached")

    serving = subparsers.add_parser("export-serving", help="write the dashboard snapshot")
    _add_date_argument(serving)

    subparsers.add_parser("reap", help="mark abandoned executions as failed")
    return parser


def _add_date_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--date", default=None, help="logical date (YYYY-MM-DD), default today")


def _add_window_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--days", type=int, default=45, help="window size in days")
    parser.add_argument("--end-date", default=None, help="last logical date (YYYY-MM-DD)")
    parser.add_argument("--volume", type=int, default=1500, help="average orders per day")
    parser.add_argument("--seed", type=int, default=42, help="deterministic seed")
    _add_date_argument(parser)


# ------------------------------------------------------------------ composition
@contextmanager
def _warehouse(settings: Settings) -> Iterator[Warehouse]:
    if settings.engine is Engine.BIGQUERY:
        from ai_dataops.adapters.bigquery import BigQueryWarehouse

        warehouse: Warehouse = BigQueryWarehouse(settings)
    else:
        warehouse = DuckDBWarehouse(settings.duckdb_path)
    try:
        initialize_schema(warehouse, settings)
        bootstrap(warehouse, settings)
        yield warehouse
    finally:
        warehouse.close()


def _resolve_date(value: str | None, settings: Settings) -> date:
    return parse_date(value) if value else business_today(settings.tzinfo)


def _rules(warehouse: Warehouse, settings: Settings) -> list[RuleDefinition]:
    return sync_rules(warehouse, settings, load_rules(settings.rules_dir, settings))


def _latest_execution(warehouse: Warehouse, settings: Settings, logical_date: date) -> Any:
    rows = warehouse.query(
        f"""
        SELECT * FROM {settings.bq_obs_dataset}.pipeline_executions
        WHERE pipeline_id = ? AND logical_date = ?
        ORDER BY attempt DESC
        LIMIT 1
        """,
        (PIPELINE_ID, logical_date),
    )
    if not rows:
        raise NotFoundError(
            "no execution for the requested date",
            context={"date": logical_date.isoformat(), "pipeline_id": PIPELINE_ID},
        )
    return rows[0]


def _run_one(
    warehouse: Warehouse,
    settings: Settings,
    logical_date: date,
    *,
    rules: Sequence[RuleDefinition],
    scenario_id: str | None = None,
    triggered_by: str = "manual",
) -> tuple[dict[str, Any], int]:
    exit_code = 0
    landing = settings.landing_dir / f"orders_{logical_date.isoformat()}.csv"
    if not landing.exists():
        generate_day(
            settings.landing_dir,
            DayPlan(logical_date, scenario_id or "normal", logical_date.toordinal()),
            volume=settings.demo_daily_volume,
        )
    try:
        run_sales_pipeline(
            warehouse,
            settings,
            logical_date,
            scenario_id=scenario_id,
            triggered_by=triggered_by,
        )
    except DataOpsError as exc:
        exit_code = 1
        logger.error("pipeline failed", extra={"date": logical_date.isoformat(), "code": exc.code})

    execution = _latest_execution(warehouse, settings, logical_date)
    outcomes = run_quality(warehouse, settings, execution, rules)
    anomalies = run_detection(warehouse, settings, execution)
    incident_id = correlate(warehouse, settings, logical_date, pipeline_id=PIPELINE_ID)

    if (
        settings.diagnosis_enabled
        and settings.engine is Engine.BIGQUERY
        and triggered_by != "backfill"
    ):
        provider = _build_provider(settings, "vertex")
        for incident in pending_incidents(warehouse, settings, logical_date=logical_date):
            diagnose_incident(warehouse, settings, str(incident["incident_id"]), provider)

    summary = {
        "date": logical_date.isoformat(),
        "execution_id": execution["execution_id"],
        "status": execution["status"],
        "scenario": execution["scenario_id"],
        "records_read": execution["records_read"],
        "records_processed": execution["records_processed"],
        "records_rejected": execution["records_rejected"],
        "rules": {
            "total": len(outcomes),
            "failed": sum(1 for item in outcomes if item.status == "fail"),
            "warn": sum(1 for item in outcomes if item.status == "warn"),
            "insufficient": sum(1 for item in outcomes if item.status == "insufficient_data"),
        },
        "anomalies": [
            {
                "metric": anomaly.metric_name,
                "observed": anomaly.observed_value,
                "expected": anomaly.expected_value,
                "severity": anomaly.severity.value,
                "method": anomaly.method,
            }
            for anomaly in anomalies
        ],
        "incident_id": incident_id,
    }
    return summary, exit_code


# ---------------------------------------------------------------------- commands
def _cmd_config(settings: Settings, *, indent: bool) -> int:
    print(json.dumps(settings.public_dict(), indent=2 if indent else None, sort_keys=True))
    return 0


def _cmd_check(settings: Settings) -> int:
    logger.info(
        "configuration valid",
        extra={"env": settings.env.value, "engine": settings.engine.value},
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "env": settings.env.value,
                "engine": settings.engine.value,
                "region": settings.region,
                "timezone": settings.timezone,
            },
            sort_keys=True,
        )
    )
    return 0


def _cmd_generate(settings: Settings, args: argparse.Namespace) -> int:
    end_date = _resolve_date(args.end_date or args.date, settings)
    plan = build_plan(end_date, args.days, inject=not args.no_inject, seed=args.seed)
    paths = generate_plan(settings.landing_dir, plan, volume=args.volume)
    injected = sum(1 for item in plan if item.scenario_id != "normal")
    print(
        json.dumps(
            {
                "generated": len(paths),
                "days": args.days,
                "end_date": end_date.isoformat(),
                "volume": args.volume,
                "seed": args.seed,
                "injected_scenarios": injected,
                "landing_dir": str(settings.landing_dir),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _cmd_backfill(settings: Settings, args: argparse.Namespace) -> int:
    end_date = _resolve_date(args.end_date or args.date, settings)
    plan = build_plan(end_date, args.days, inject=not args.no_inject, seed=args.seed)
    generate_plan(settings.landing_dir, plan, volume=args.volume)
    with _warehouse(settings) as warehouse:
        rules = _rules(warehouse, settings)
        results = []
        for item in plan:
            summary, _ = _run_one(
                warehouse,
                settings,
                item.logical_date,
                rules=rules,
                scenario_id=item.scenario_id,
                triggered_by="backfill",
            )
            results.append(summary)
    failed = sum(1 for item in results if item["status"] == "failed")
    print(
        json.dumps(
            {
                "days": len(results),
                "failed_executions": failed,
                "incidents": sum(1 for item in results if item["incident_id"]),
                "end_date": end_date.isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _cmd_backtest(settings: Settings, args: argparse.Namespace) -> int:
    end_date = _resolve_date(args.end_date or args.date, settings)
    with _warehouse(settings) as warehouse:
        report = run_backtest(
            warehouse,
            settings,
            end_date=end_date,
            days=args.days,
            warmup_days=args.warmup_days,
        )
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0


def _cmd_demo(settings: Settings, args: argparse.Namespace) -> int:
    exit_code = _cmd_backfill(settings, args)
    if exit_code != 0:
        return exit_code
    return _cmd_backtest(settings, args)


def _cmd_run_pipeline(settings: Settings, args: argparse.Namespace) -> int:
    logical_date = _resolve_date(args.date, settings)
    with _warehouse(settings) as warehouse:
        execution_id = run_sales_pipeline(
            warehouse,
            settings,
            logical_date,
            scenario_id=args.scenario,
            triggered_by="manual",
        )
        execution = _latest_execution(warehouse, settings, logical_date)
    print(
        json.dumps(
            {
                "execution_id": execution_id,
                "status": execution["status"],
                "records_processed": execution["records_processed"],
                "records_rejected": execution["records_rejected"],
            },
            sort_keys=True,
        )
    )
    return 0 if execution["status"] == "succeeded" else 1


def _cmd_run_quality(settings: Settings, args: argparse.Namespace) -> int:
    logical_date = _resolve_date(args.date, settings)
    with _warehouse(settings) as warehouse:
        execution = _latest_execution(warehouse, settings, logical_date)
        outcomes = run_quality(warehouse, settings, execution, _rules(warehouse, settings))
    print(
        json.dumps(
            {
                "rules": len(outcomes),
                "failed": [item.rule_id for item in outcomes if item.status == "fail"],
                "warn": [item.rule_id for item in outcomes if item.status == "warn"],
                "insufficient": [
                    item.rule_id for item in outcomes if item.status == "insufficient_data"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _cmd_detect(settings: Settings, args: argparse.Namespace) -> int:
    logical_date = _resolve_date(args.date, settings)
    with _warehouse(settings) as warehouse:
        execution = _latest_execution(warehouse, settings, logical_date)
        anomalies = run_detection(warehouse, settings, execution)
    print(
        json.dumps(
            {
                "date": logical_date.isoformat(),
                "anomalies": len(anomalies),
                "severity": [item.severity.value for item in anomalies],
            },
            sort_keys=True,
        )
    )
    return 0


def _cmd_correlate(settings: Settings, args: argparse.Namespace) -> int:
    logical_date = _resolve_date(args.date, settings)
    with _warehouse(settings) as warehouse:
        incident_id = correlate(warehouse, settings, logical_date, pipeline_id=PIPELINE_ID)
    print(json.dumps({"date": logical_date.isoformat(), "incident_id": incident_id}))
    return 0


def _build_provider(settings: Settings, name: str) -> DiagnosisProvider:
    if name == "fake":
        return FakeDiagnosisProvider()
    from ai_dataops.adapters.gemini import VertexGeminiProvider

    return VertexGeminiProvider(settings)


def _cmd_diagnose(settings: Settings, args: argparse.Namespace) -> int:
    provider = _build_provider(settings, args.provider)
    logical_date = _resolve_date(args.date, settings) if args.date else None
    with _warehouse(settings) as warehouse:
        if args.incident:
            incident_ids = [args.incident]
        else:
            incident_ids = [
                str(item["incident_id"])
                for item in pending_incidents(
                    warehouse, settings, logical_date=logical_date, limit=args.limit
                )
            ]
        logger.info(
            "diagnosis pending",
            extra={"pending": len(incident_ids), "logical_date": str(logical_date)},
        )
        results = []
        for incident_id in incident_ids:
            outcome = diagnose_incident(
                warehouse, settings, incident_id, provider, force=args.force
            )
            results.append(
                {
                    "incident_id": incident_id,
                    "status": outcome.status,
                    "diagnosis_id": outcome.diagnosis_id,
                    "cached": outcome.cached,
                    "cost_usd": outcome.cost_usd,
                    "latency_ms": outcome.latency_ms,
                    "confidence": outcome.payload.confidence if outcome.payload else None,
                    "top_hypothesis": outcome.payload.hypotheses[0].cause
                    if outcome.payload and outcome.payload.hypotheses
                    else None,
                    "error": outcome.error,
                }
            )
    print(json.dumps({"provider": args.provider, "results": results}, indent=2, sort_keys=True))
    return 0 if all(item["status"] in {"success", "cached"} for item in results) else 1


def _cmd_evaluate_diagnosis(settings: Settings, args: argparse.Namespace) -> int:
    provider = _build_provider(settings, args.provider)
    with _warehouse(settings) as warehouse:
        report = evaluate_diagnoses(
            warehouse,
            settings,
            provider,
            limit=args.limit,
            force=args.force,
        )
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0


def _publish_snapshot(
    settings: Settings,
    warehouse: Warehouse,
    logical_date: date | None = None,
) -> tuple[str, str | None]:
    path = export_snapshot(warehouse, settings, end_date=logical_date)
    uri = None
    if settings.snapshot_bucket:
        uri = upload_text(
            settings.snapshot_bucket, "serving/snapshot.json", path.read_text(encoding="utf-8")
        )
    return str(path), uri


def _cmd_export_serving(settings: Settings, args: argparse.Namespace) -> int:
    logical_date = _resolve_date(args.date, settings) if args.date else None
    with _warehouse(settings) as warehouse:
        path, uri = _publish_snapshot(settings, warehouse, logical_date)
    print(
        json.dumps(
            {
                "snapshot": path,
                "uri": uri,
                "size_bytes": Path(path).stat().st_size,
            },
            sort_keys=True,
        )
    )
    return 0


def _cmd_status(settings: Settings, args: argparse.Namespace) -> int:
    logical_date = _resolve_date(args.date, settings)
    with _warehouse(settings) as warehouse:
        execution = _latest_execution(warehouse, settings, logical_date)
        rules = warehouse.query(
            f"SELECT status FROM {settings.bq_quality_dataset}.quality_rule_results "
            "WHERE execution_id = ?",
            (execution["execution_id"],),
        )
        anomalies = warehouse.query(
            f"SELECT metric_name, severity FROM {settings.bq_detect_dataset}.anomalies "
            "WHERE execution_id = ?",
            (execution["execution_id"],),
        )
        incidents = warehouse.query(
            f"SELECT incident_id, severity, title FROM {settings.bq_detect_dataset}.incidents "
            "WHERE CAST(bucket_start AS DATE) = ?",
            (logical_date,),
        )
    print(
        json.dumps(
            {
                "execution": {
                    "id": execution["execution_id"],
                    "status": execution["status"],
                    "scenario": execution["scenario_id"],
                    "records_processed": execution["records_processed"],
                    "records_rejected": execution["records_rejected"],
                },
                "rules": {
                    "total": len(rules),
                    "failed": sum(1 for item in rules if item["status"] == "fail"),
                },
                "anomalies": [
                    {"metric": item["metric_name"], "severity": item["severity"]}
                    for item in anomalies
                ],
                "incidents": incidents,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


# -------------------------------------------------------------------------- main
def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        print(f"ai-dataops {__version__}")
        return 0

    try:
        settings = load_settings()
    except DataOpsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code

    configure_logging(settings.log_level, settings.log_format)

    if args.command == "config":
        return _cmd_config(settings, indent=not args.no_indent)
    if args.command == "check":
        return _cmd_check(settings)

    try:
        if args.command == "generate":
            return _cmd_generate(settings, args)
        if args.command == "backfill":
            return _cmd_backfill(settings, args)
        if args.command == "backtest":
            return _cmd_backtest(settings, args)
        if args.command == "demo":
            return _cmd_demo(settings, args)
        if args.command == "bootstrap":
            with _warehouse(settings):
                print(json.dumps({"status": "bootstrapped"}))
            return 0
        if args.command == "diagnose":
            return _cmd_diagnose(settings, args)
        if args.command == "evaluate-diagnosis":
            return _cmd_evaluate_diagnosis(settings, args)
        if args.command == "export-serving":
            return _cmd_export_serving(settings, args)
        if args.command == "reap":
            with _warehouse(settings) as warehouse:
                reaped = reap_stale_runs(warehouse, settings)
            print(json.dumps({"reaped": reaped}))
            return 0
        if args.command == "run-pipeline":
            return _cmd_run_pipeline(settings, args)
        if args.command == "run-quality":
            return _cmd_run_quality(settings, args)
        if args.command == "detect":
            return _cmd_detect(settings, args)
        if args.command == "correlate":
            return _cmd_correlate(settings, args)
        if args.command == "status":
            return _cmd_status(settings, args)
        if args.command == "run-daily":
            logical_date = _resolve_date(args.date, settings)
            with _warehouse(settings) as warehouse:
                summary, exit_code = _run_one(
                    warehouse,
                    settings,
                    logical_date,
                    rules=_rules(warehouse, settings),
                    scenario_id=args.scenario,
                    triggered_by="manual",
                )
                _publish_snapshot(settings, warehouse, logical_date)
            print(json.dumps(summary, indent=2, sort_keys=True))
            return exit_code
    except DataOpsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
