"""Tests for structured logging and context binding."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from ai_dataops.config import LogFormat
from ai_dataops.logging import bind_context, configure_logging, get_logger


@pytest.fixture(autouse=True)
def _restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    previous_level = root.level
    previous_handlers = list(root.handlers)
    yield
    root.handlers = previous_handlers
    root.setLevel(previous_level)


def _payloads(stream: io.StringIO) -> list[dict[str, object]]:
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def test_json_record_contains_expected_fields(isolated_env: Path) -> None:
    stream = io.StringIO()
    configure_logging("INFO", LogFormat.JSON, stream=stream)

    get_logger("tests").info("hello", extra={"duration_ms": 12})

    payload = _payloads(stream)[0]
    assert payload["severity"] == "INFO"
    assert payload["logger"] == "ai_dataops.tests"
    assert payload["message"] == "hello"
    assert payload["duration_ms"] == 12
    assert str(payload["timestamp"]).endswith("Z")


def test_bound_context_is_merged_and_reset(isolated_env: Path) -> None:
    stream = io.StringIO()
    configure_logging("INFO", LogFormat.JSON, stream=stream)
    logger = get_logger("tests")

    with bind_context(run_key="sales_daily:2026-11-17", stage="quality"):
        logger.info("inside")

    logger.info("outside")

    inside, outside = _payloads(stream)
    assert inside["run_key"] == "sales_daily:2026-11-17"
    assert inside["stage"] == "quality"
    assert "run_key" not in outside
    assert "stage" not in outside


def test_nested_context_overrides_and_restores(isolated_env: Path) -> None:
    stream = io.StringIO()
    configure_logging("INFO", LogFormat.JSON, stream=stream)
    logger = get_logger("tests")

    with bind_context(stage="pipeline"):
        with bind_context(stage="quality"):
            logger.info("inner")
        logger.info("outer")

    inner, outer = _payloads(stream)
    assert inner["stage"] == "quality"
    assert outer["stage"] == "pipeline"


def test_repeated_configuration_does_not_duplicate_handlers(isolated_env: Path) -> None:
    stream = io.StringIO()
    configure_logging("INFO", LogFormat.JSON, stream=stream)
    configure_logging("INFO", LogFormat.JSON, stream=stream)

    get_logger("tests").info("once")

    assert len(_payloads(stream)) == 1


def test_console_format_is_human_readable(isolated_env: Path) -> None:
    stream = io.StringIO()
    configure_logging("INFO", LogFormat.CONSOLE, stream=stream)

    with bind_context(stage="check"):
        get_logger("tests").warning("careful")

    output = stream.getvalue()
    assert "WARNING" in output
    assert "careful" in output
    assert "stage=check" in output


def test_exception_information_is_serialised(isolated_env: Path) -> None:
    stream = io.StringIO()
    configure_logging("INFO", LogFormat.JSON, stream=stream)

    try:
        raise ValueError("boom")
    except ValueError:
        get_logger("tests").exception("failed")

    payload = _payloads(stream)[0]
    assert payload["message"] == "failed"
    assert str(payload["exception"]).startswith("Traceback")
    assert "ValueError: boom" in str(payload["exception"])


def test_get_logger_accepts_short_and_full_names() -> None:
    assert get_logger("config").name == "ai_dataops.config"
    assert get_logger("ai_dataops.quality").name == "ai_dataops.quality"
    assert get_logger().name == "ai_dataops"
