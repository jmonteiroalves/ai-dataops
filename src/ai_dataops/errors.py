"""Typed exception hierarchy shared by every layer.

Exit codes follow the stage contract in ``docs/architecture.md``:
``1`` for business/data failures and ``2`` for infrastructure or configuration failures.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class DataOpsError(Exception):
    """Base class for all expected, domain-level failures."""

    code: str = "dataops_error"
    exit_code: int = 1

    def __init__(self, message: str, *, context: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = dict(context or {})

    def to_dict(self) -> dict[str, Any]:
        """Serialisable representation for structured logs and CLI output."""
        payload: dict[str, Any] = {"error": self.code, "message": self.message}
        if self.context:
            payload["context"] = self.context
        return payload

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class ConfigurationError(DataOpsError):
    """Invalid or incomplete settings. Always an infrastructure-class failure."""

    code = "configuration_error"
    exit_code = 2


class DataValidationError(DataOpsError):
    """Input violated an expected contract (shape, type, domain)."""

    code = "data_validation_error"
    exit_code = 1


class NotFoundError(DataOpsError):
    """A requested entity does not exist."""

    code = "not_found"
    exit_code = 1


class ExternalServiceError(DataOpsError):
    """A dependency failed or was unavailable. Retryable at the caller's discretion."""

    code = "external_service_error"
    exit_code = 2


class WarehouseError(ExternalServiceError):
    """The query engine (DuckDB or BigQuery) failed."""

    code = "warehouse_error"


class ProviderError(ExternalServiceError):
    """A model provider (Gemini) failed, timed out or returned an unusable response."""

    code = "provider_error"


class DiagnosisValidationError(DataValidationError):
    """The model response violated the diagnosis contract."""

    code = "diagnosis_invalid"


class SchemaError(DataValidationError):
    """A structural contract was broken (missing column, incompatible type)."""

    code = "schema_error"


__all__ = [
    "ConfigurationError",
    "DataOpsError",
    "DataValidationError",
    "DiagnosisValidationError",
    "ExternalServiceError",
    "NotFoundError",
    "ProviderError",
    "SchemaError",
    "WarehouseError",
]
