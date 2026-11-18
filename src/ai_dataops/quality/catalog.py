"""Quality rule catalog: YAML in, immutable typed rules out."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import yaml

from ai_dataops.config import Settings
from ai_dataops.domain.enums import QualityDimension, Severity
from ai_dataops.domain.models import RuleDefinition
from ai_dataops.domain.ports import Warehouse
from ai_dataops.errors import ConfigurationError
from ai_dataops.timeutil import utcnow

SUPPORTED_TYPES = frozenset(
    {
        "not_null",
        "unique",
        "accepted_values",
        "regex_match",
        "range",
        "expression",
        "foreign_key",
        "freshness",
        "schema_contract",
    }
)

_REQUIRED_FIELDS = ("rule_id", "name", "dataset", "dimension", "rule_type", "severity", "threshold")


def _resolve(name: str, settings: Settings) -> str:
    return name if "." in name else f"{settings.demo_dataset}.{name}"


def config_hash(rule: RuleDefinition) -> str:
    payload = {
        "rule_type": rule.rule_type,
        "columns": list(rule.columns),
        "config": rule.config,
        "threshold": rule.threshold,
        "warn_threshold": rule.warn_threshold,
        "weight": rule.weight,
        "severity": rule.severity.value,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def load_rules(rules_dir: Path, settings: Settings) -> list[RuleDefinition]:
    if not rules_dir.exists():
        raise ConfigurationError(f"rules directory not found: {rules_dir}")

    rules: list[RuleDefinition] = []
    for path in sorted(rules_dir.rglob("*.yml")) + sorted(rules_dir.rglob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ConfigurationError(f"invalid rule file: {path}")
        missing = [field for field in _REQUIRED_FIELDS if field not in raw]
        if missing:
            raise ConfigurationError(f"{path}: missing fields {missing}")
        rule_type = str(raw["rule_type"])
        if rule_type not in SUPPORTED_TYPES:
            raise ConfigurationError(f"{path}: unsupported rule_type {rule_type}")
        try:
            dimension = QualityDimension(str(raw["dimension"]))
            severity = Severity(str(raw["severity"]))
        except ValueError as exc:
            raise ConfigurationError(f"{path}: invalid dimension/severity") from exc

        config = dict(raw.get("config") or {})
        if "ref_dataset" in config:
            config["ref_dataset"] = _resolve(str(config["ref_dataset"]), settings)
        columns = tuple(str(column) for column in raw.get("columns") or ())

        rule = RuleDefinition(
            rule_id=str(raw["rule_id"]),
            name=str(raw["name"]),
            dataset_key=_resolve(str(raw["dataset"]), settings),
            dimension=dimension,
            rule_type=rule_type,
            columns=columns,
            config=config,
            threshold=float(raw["threshold"]),
            warn_threshold=(
                float(raw["warn_threshold"]) if raw.get("warn_threshold") is not None else None
            ),
            weight=float(raw.get("weight", 1.0)),
            severity=severity,
            owner=str(raw["owner"]) if raw.get("owner") else None,
            description=str(raw["description"]).strip() if raw.get("description") else None,
            is_active=bool(raw.get("is_active", True)),
            version=1,
            config_hash="",
        )
        rules.append(replace(rule, config_hash=config_hash(rule)))
    return rules


def sync_rules(
    warehouse: Warehouse,
    settings: Settings,
    rules: list[RuleDefinition],
) -> list[RuleDefinition]:
    """Upsert rule definitions, assigning a new immutable version when the hash changes."""
    meta = settings.bq_meta_dataset
    now = utcnow()
    synced: list[RuleDefinition] = []

    for rule in rules:
        latest = warehouse.query(
            f"""
            SELECT version, config_hash
            FROM {meta}.quality_rule_versions
            WHERE rule_id = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (rule.rule_id,),
        )
        if not latest:
            version = 1
        elif latest[0]["config_hash"] == rule.config_hash:
            version = int(latest[0]["version"])
        else:
            version = int(latest[0]["version"]) + 1
            warehouse.execute(
                f"UPDATE {meta}.quality_rule_versions SET valid_to = ? "
                f"WHERE rule_id = ? AND valid_to IS NULL",
                (now, rule.rule_id),
            )

        current = warehouse.query(
            f"SELECT current_version FROM {meta}.quality_rules WHERE rule_id = ?",
            (rule.rule_id,),
        )
        needs_version_row = not latest or version != int(latest[0]["version"])
        if needs_version_row:
            warehouse.write_rows(
                f"{meta}.quality_rule_versions",
                [
                    {
                        "rule_id": rule.rule_id,
                        "version": version,
                        "rule_type": rule.rule_type,
                        "columns_json": list(rule.columns),
                        "config_json": rule.config,
                        "config_hash": rule.config_hash,
                        "threshold": rule.threshold,
                        "warn_threshold": rule.warn_threshold,
                        "weight": rule.weight,
                        "severity": rule.severity.value,
                        "change_reason": "catalog sync",
                        "created_by": "catalog",
                        "valid_from": now,
                        "valid_to": None,
                    }
                ],
            )

        created_at = now
        if current:
            existing = warehouse.query(
                f"SELECT created_at FROM {meta}.quality_rules WHERE rule_id = ?",
                (rule.rule_id,),
            )
            if existing:
                created_at = existing[0]["created_at"]
        warehouse.write_rows(
            f"{meta}.quality_rules",
            [
                {
                    "rule_id": rule.rule_id,
                    "name": rule.name,
                    "dataset_key": rule.dataset_key,
                    "dimension": rule.dimension.value,
                    "current_version": version,
                    "severity": rule.severity.value,
                    "owner": rule.owner,
                    "is_active": rule.is_active,
                    "created_at": created_at,
                    "updated_at": now,
                }
            ],
        )
        synced.append(replace(rule, version=version))

    return synced


__all__ = ["SUPPORTED_TYPES", "config_hash", "load_rules", "sync_rules"]
