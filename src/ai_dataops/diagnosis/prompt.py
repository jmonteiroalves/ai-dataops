"""Versioned prompt loading.

Prompt files are immutable per version. If the content hash of an existing version changes,
the loader fails instead of silently rewriting history; a new version directory is required.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ai_dataops.config import Settings
from ai_dataops.domain.ports import Warehouse
from ai_dataops.errors import ConfigurationError
from ai_dataops.timeutil import utcnow

PROMPT_ID = "incident_diagnosis"
OUTPUT_SCHEMA_VERSION = "diagnosis.v1"


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    prompt_id: str
    version: int
    model_id: str
    system: str
    template: str
    output_schema_version: str
    content_hash: str

    def render(self, bundle_json: str) -> str:
        return self.template.replace("{{bundle}}", bundle_json)


def load_prompt(settings: Settings, version: int = 3) -> PromptTemplate:
    directory = settings.prompts_dir / PROMPT_ID / f"v{version}"
    system_path = directory / "system.md"
    template_path = directory / "user.md"
    if not system_path.exists() or not template_path.exists():
        raise ConfigurationError("prompt version not found", context={"directory": str(directory)})
    system = system_path.read_text(encoding="utf-8").strip()
    template = template_path.read_text(encoding="utf-8").strip()
    content_hash = hashlib.sha256(
        json.dumps(
            {
                "system": system,
                "template": template,
                "schema": OUTPUT_SCHEMA_VERSION,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return PromptTemplate(
        prompt_id=PROMPT_ID,
        version=version,
        model_id=settings.gemini_model,
        system=system,
        template=template,
        output_schema_version=OUTPUT_SCHEMA_VERSION,
        content_hash=content_hash,
    )


def ensure_prompt_version(
    warehouse: Warehouse,
    settings: Settings,
    prompt: PromptTemplate,
) -> PromptTemplate:
    meta = settings.bq_meta_dataset
    rows = warehouse.query(
        f"SELECT version, content_hash FROM {meta}.prompt_versions "
        "WHERE prompt_id = ? ORDER BY version DESC LIMIT 1",
        (prompt.prompt_id,),
    )
    if rows:
        latest_version = int(rows[0]["version"])
        if latest_version > prompt.version:
            raise ConfigurationError(
                "prompt version is older than the active one",
                context={"file": prompt.version, "database": latest_version},
            )
        if latest_version == prompt.version:
            if rows[0]["content_hash"] != prompt.content_hash:
                raise ConfigurationError(
                    "prompt content changed without a new version; create the next vN directory",
                    context={"prompt_id": prompt.prompt_id, "version": prompt.version},
                )
            return prompt
        warehouse.execute(
            f"UPDATE {meta}.prompt_versions SET is_active = FALSE "
            "WHERE prompt_id = ? AND is_active = TRUE",
            (prompt.prompt_id,),
        )

    warehouse.write_rows(
        f"{meta}.prompt_versions",
        [
            {
                "prompt_id": prompt.prompt_id,
                "version": prompt.version,
                "model_id": prompt.model_id,
                "system_instructions": prompt.system,
                "template": prompt.template,
                "output_schema_version": prompt.output_schema_version,
                "content_hash": prompt.content_hash,
                "is_active": True,
                "created_at": utcnow(),
            }
        ],
    )
    return prompt


def ensure_model(warehouse: Warehouse, settings: Settings, prompt: PromptTemplate) -> None:
    meta = settings.bq_meta_dataset
    rows = warehouse.query(
        f"SELECT 1 FROM {meta}.llm_models WHERE model_id = ? LIMIT 1",
        (prompt.model_id,),
    )
    if rows:
        return
    warehouse.write_rows(
        f"{meta}.llm_models",
        [
            {
                "model_id": prompt.model_id,
                "provider": "vertex_ai",
                "display_name": prompt.model_id,
                "input_price_per_mtok": 0.30,
                "output_price_per_mtok": 2.50,
                "valid_from": utcnow(),
                "valid_to": None,
            }
        ],
    )


def prompt_path(settings: Settings, version: int) -> Path:
    return settings.prompts_dir / PROMPT_ID / f"v{version}"


__all__ = [
    "PROMPT_ID",
    "PromptTemplate",
    "ensure_model",
    "ensure_prompt_version",
    "load_prompt",
    "prompt_path",
]
