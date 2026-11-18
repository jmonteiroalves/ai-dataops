"""Architectural guardrails: layering and dependency boundaries.

These tests encode rules from AGENTS.md, so a violation fails CI instead of
being caught in review.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "ai_dataops"


def _modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def test_cloud_sdks_are_confined_to_adapters() -> None:
    violations: list[str] = []
    for path in _modules():
        relative = path.relative_to(SRC)
        if "adapters" in relative.parts:
            continue
        for module in _imported_modules(path):
            if module == "google" or module.startswith("google."):
                violations.append(f"{relative}: {module}")

    assert violations == [], f"cloud SDK imports outside adapters: {violations}"


def test_domain_does_not_depend_on_infrastructure_or_config() -> None:
    violations: list[str] = []
    for path in sorted((SRC / "domain").rglob("*.py")):
        for module in _imported_modules(path):
            if module.startswith(("ai_dataops.adapters", "ai_dataops.config", "ai_dataops.cli")):
                violations.append(f"{path.name}: {module}")

    assert violations == []


def test_package_is_marked_as_typed() -> None:
    assert (SRC / "py.typed").exists()


def test_adapters_package_exists() -> None:
    import ai_dataops.adapters as adapters

    assert (SRC / "adapters" / "__init__.py").exists()
    assert adapters.__all__ == []
