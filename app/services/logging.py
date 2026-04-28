"""
Structured JSON logging for connect-manager.

K8s log collectors (fluent-bit / CloudWatch agent) ingest raw stdout, so we
emit one JSON object per line. Required fields per CLAUDE.md "Conventions →
Python": ``timestamp``, ``level``, ``logger``, ``message``. Contextual fields
``request_id``, ``user_id``, and ``job_id`` are propagated via
``contextvars.ContextVar`` so a FastAPI middleware (set per request) and
ARQ hooks (set per job) can attach correlation IDs without threading them
through every call site.

Stdlib only — no third-party deps. ``LOG_FORMAT=text`` is honored as a
human-friendly escape hatch for ``kubectl logs`` debugging; production
defaults to JSON.

Wiring into ``app/main.py`` and FastAPI/ARQ middleware happens in later
beads; this module just exposes the primitives.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Final

__all__ = [
    "configure_logging",
    "get_logger",
    "set_context",
    "clear_context",
]


# Contextvars carry per-request / per-job correlation IDs through async tasks.
# Setting via ``set_context`` is opt-in; absent values are simply omitted from
# the emitted JSON.
_request_id_var: ContextVar[str | None] = ContextVar("connect_request_id", default=None)
_user_id_var: ContextVar[str | None] = ContextVar("connect_user_id", default=None)
_job_id_var: ContextVar[str | None] = ContextVar("connect_job_id", default=None)

_CONTEXT_VARS: Final[dict[str, ContextVar[str | None]]] = {
    "request_id": _request_id_var,
    "user_id": _user_id_var,
    "job_id": _job_id_var,
}

# LogRecord attributes that are part of the stdlib record contract; anything
# else attached via ``extra=`` is treated as a user-supplied field and merged
# into the JSON payload.
_RESERVED_RECORD_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def _format_timestamp(created: float) -> str:
    """Format a ``LogRecord.created`` epoch float as ISO 8601 UTC with ms."""
    dt = datetime.fromtimestamp(created, tz=timezone.utc)
    # Trim microseconds to milliseconds and append a Z suffix per the
    # CLAUDE.md example (``2026-04-27T20:34:18.123Z``).
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class JsonFormatter(logging.Formatter):
    """Format a ``LogRecord`` as a single-line JSON object.

    Always emits ``timestamp``, ``level``, ``logger``, and ``message``. Adds
    ``request_id`` / ``user_id`` / ``job_id`` when their contextvars are set.
    Merges any non-reserved ``extra=`` fields. On exceptions includes
    ``exception_type``, ``exception_message``, and ``traceback``.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _format_timestamp(record.created),
            "level": record.levelname.upper(),
            "logger": record.name,
            "message": record.getMessage(),
        }

        for field, var in _CONTEXT_VARS.items():
            value = var.get()
            if value is not None:
                payload[field] = value

        # Merge user-supplied ``extra=`` fields. Caller-supplied fields take
        # precedence over context fields if names collide — this matches
        # stdlib ``logging`` semantics for ``extra``.
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_ATTRS or key.startswith("_"):
                continue
            payload[key] = value

        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info
            payload["exception_type"] = (
                exc_type.__name__ if exc_type is not None else None
            )
            payload["exception_message"] = str(exc_value) if exc_value is not None else None
            payload["traceback"] = "".join(
                traceback.format_exception(exc_type, exc_value, exc_tb)
            )
        elif record.exc_text:
            payload["traceback"] = record.exc_text

        if record.stack_info:
            payload["stack_info"] = record.stack_info

        return json.dumps(payload, default=str, ensure_ascii=False)


def _resolve_level(level: str) -> int:
    env_level = os.environ.get("LOG_LEVEL")
    chosen = (env_level or level).upper()
    numeric = logging.getLevelName(chosen)
    if not isinstance(numeric, int):
        # Unknown level string — fall back to INFO rather than raising. The
        # logger should never be the thing that crashes the app at startup.
        return logging.INFO
    return numeric


def configure_logging(level: str = "INFO") -> None:
    """Install the structured logger on the root logger.

    Removes any pre-existing handlers so uvicorn / FastAPI defaults don't
    double-emit. ``LOG_LEVEL`` env var, if set, overrides the ``level``
    argument. ``LOG_FORMAT=text`` falls back to the stdlib default human
    formatter for local ``kubectl logs`` debugging; any other value (or
    unset) yields JSON.

    Idempotent: calling this multiple times leaves exactly one handler on
    the root logger.
    """
    numeric_level = _resolve_level(level)
    log_format = os.environ.get("LOG_FORMAT", "json").lower()

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)

    handler = logging.StreamHandler(stream=sys.stdout)
    if log_format == "text":
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s %(name)s - %(message)s",
            )
        )
    else:
        handler.setFormatter(JsonFormatter())
    handler.setLevel(numeric_level)

    root.addHandler(handler)
    root.setLevel(numeric_level)


def get_logger(name: str) -> logging.Logger:
    """Return ``logging.getLogger(name)`` so callers don't import logging."""
    return logging.getLogger(name)


def set_context(
    *,
    request_id: str | None = None,
    user_id: str | None = None,
    job_id: str | None = None,
) -> None:
    """Set one or more contextual fields for the current async task.

    Only keyword arguments that are non-None are applied; pass an explicit
    ``None`` is intentionally treated as "leave alone" rather than "clear" —
    use :func:`clear_context` for clearing.
    """
    if request_id is not None:
        _request_id_var.set(request_id)
    if user_id is not None:
        _user_id_var.set(user_id)
    if job_id is not None:
        _job_id_var.set(job_id)


def clear_context() -> None:
    """Reset all contextual fields (request_id, user_id, job_id) to None."""
    _request_id_var.set(None)
    _user_id_var.set(None)
    _job_id_var.set(None)
