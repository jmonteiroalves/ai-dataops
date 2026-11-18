"""Structured logging with per-run context.

Every log line is JSON in production (Cloud Logging friendly) and one line of
text locally. ``bind_context`` attaches ``run_key``, ``stage``, ``execution_id``
and similar fields to every record emitted inside the block, without threading
them through function signatures.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, TextIO

from ai_dataops.config.settings import LogFormat

_LOGGER_NAME = "ai_dataops"

_context: ContextVar[dict[str, Any] | None] = ContextVar("ai_dataops_context", default=None)

_RESERVED_ATTRIBUTES: frozenset[str] = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None))
) | {"message", "asctime", "taskName"}


def _current_context() -> dict[str, Any]:
    return _context.get() or {}


class _DataOpsHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Marker subclass so reconfiguration replaces only handlers we created."""


class JsonFormatter(logging.Formatter):
    """Render a log record as a single JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_current_context())
        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRIBUTES and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable format that still shows the bound context."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-8s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        context = _current_context()
        if not context:
            return base
        suffix = " ".join(f"{key}={value}" for key, value in sorted(context.items()))
        return f"{base} [{suffix}]"


def configure_logging(
    level: str,
    log_format: LogFormat | str,
    *,
    stream: TextIO | None = None,
) -> None:
    """Configure the root logger once, replacing handlers created by this module.

    Logs are written to ``stream`` (stderr by default), so command output on
    stdout stays machine-parseable.
    """
    fmt = log_format if isinstance(log_format, LogFormat) else LogFormat(log_format)
    formatter: logging.Formatter = JsonFormatter() if fmt is LogFormat.JSON else ConsoleFormatter()

    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, _DataOpsHandler):
            root.removeHandler(handler)

    handler = _DataOpsHandler(stream)
    handler.setFormatter(formatter)

    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a namespaced logger, accepting ``"config"`` or a full dotted name."""
    if not name:
        return logging.getLogger(_LOGGER_NAME)
    if name == _LOGGER_NAME or name.startswith(f"{_LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")


@contextmanager
def bind_context(**fields: Any) -> Iterator[None]:
    """Attach fields to every log record emitted inside the block."""
    current = dict(_current_context())
    current.update(fields)
    token = _context.set(current)
    try:
        yield
    finally:
        _context.reset(token)


__all__ = ["bind_context", "configure_logging", "get_logger"]
