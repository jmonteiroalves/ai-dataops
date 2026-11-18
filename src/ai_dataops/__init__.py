"""AI DataOps: data observability and AI-assisted incident diagnosis.

The package is organised by feature, with infrastructure isolated under
``ai_dataops.adapters``. Business modules must never import cloud provider SDKs.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ai-dataops")
except PackageNotFoundError:  # pragma: no cover - source tree without installation
    __version__ = "0.0.0"

__all__ = ["__version__"]
