"""Tests for the domain exception hierarchy."""

from __future__ import annotations

from ai_dataops.errors import (
    ConfigurationError,
    DataOpsError,
    DataValidationError,
    ExternalServiceError,
    NotFoundError,
    ProviderError,
    SchemaError,
    WarehouseError,
)


def test_base_error_defaults() -> None:
    error = DataOpsError("something broke")

    assert error.code == "dataops_error"
    assert error.exit_code == 1
    assert error.to_dict() == {"error": "dataops_error", "message": "something broke"}
    assert str(error) == "[dataops_error] something broke"


def test_context_is_serialised_and_copied() -> None:
    source = {"field": "engine"}
    error = DataValidationError("bad input", context=source)
    source["field"] = "mutated"

    assert error.to_dict()["context"] == {"field": "engine"}


def test_configuration_errors_use_the_infrastructure_exit_code() -> None:
    assert ConfigurationError("x").exit_code == 2
    assert WarehouseError("x").exit_code == 2
    assert ProviderError("x").exit_code == 2


def test_hierarchy_is_preserved() -> None:
    assert issubclass(WarehouseError, ExternalServiceError)
    assert issubclass(SchemaError, DataValidationError)
    assert issubclass(NotFoundError, DataOpsError)
    assert WarehouseError.code == "warehouse_error"
    assert SchemaError.code == "schema_error"
