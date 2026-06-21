"""Centralized JSON logger for all app modules.

Conforms to ~/.claude/rules/06-logging.md:
- One JSON object per line
- ISO 8601 UTC timestamp with microsecond precision (`time`)
- Nested `source.{function,file,line}`
- `level`, `msg`
- `trace_id` on every line, pulled from ContextVar
- `request_id` on HTTP request-scoped lines (echoed in X-Request-Id)
- Key-based redaction at format time so callers can log liberally
- Default sink is stderr (Docker captures stderr to docker logs)
- Optional rotating file at $LOG_FILE if set

Usage:
    from logger import get_logger, request_id_var, trace_id_var
    log = get_logger(__name__)
    log.info("processed", extra={"order_id": 42, "amount_cents": 1999})

ContextVar scope (set by middleware, read automatically by the formatter):
    trace_id_var.set("<ulid>")
    request_id_var.set("<incoming or generated UUID>")

Never log raw secrets — but the redactor catches keys matching
password / token / secret / api_key / authorization / cookie / set-cookie
at format time so accidental inclusion in headers / DSNs / request bodies
is masked to "[REDACTED]".
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any

# =============================================================================
# CONTEXT VARS — populated by middleware / request handlers, read by formatter
# =============================================================================

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def new_trace_id() -> str:
    """Generate a fresh trace id. UUID4 hex — 32 chars, no dashes."""
    return uuid.uuid4().hex


def ensure_trace_id() -> str:
    """Read the current trace_id; mint one if unset, set it on the contextvar."""
    tid = trace_id_var.get()
    if tid:
        return tid
    tid = new_trace_id()
    trace_id_var.set(tid)
    return tid


# =============================================================================
# REDACTION
# =============================================================================

_REDACT_KEY_RE = re.compile(
    r"^(password|passwd|pwd|token|secret|api[_-]?key|authorization|cookie|set-cookie|"
    r"auth[_-]?token|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"private[_-]?key|session)$",
    re.IGNORECASE,
)

_REDACTED = "[REDACTED]"


def _redact(value: Any) -> Any:
    """Recursively walk lists/dicts replacing values for sensitive keys."""
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _REDACT_KEY_RE.match(str(k)) else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


# =============================================================================
# FORMATTER
# =============================================================================


class _JSONFormatter(logging.Formatter):
    """One JSON object per line, conforming to 06-logging.md."""

    def format(self, record: logging.LogRecord) -> str:
        # ISO 8601 UTC with microsecond precision (Z suffix).
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
            timespec="microseconds"
        )
        if ts.endswith("+00:00"):
            ts = ts[:-6] + "Z"

        entry: dict[str, Any] = {
            "time": ts,
            "level": record.levelname,
            "source": {
                "function": record.funcName,
                "file": record.module,
                "line": record.lineno,
            },
            "msg": record.getMessage(),
            "trace_id": trace_id_var.get(),
        }

        request_id = request_id_var.get()
        if request_id:
            entry["request_id"] = request_id

        # Merge structured extras passed via extra={...}, redacting sensitive keys.
        for k, v in record.__dict__.items():
            if k in _RESERVED or k in entry:
                continue
            if _REDACT_KEY_RE.match(k):
                entry[k] = _REDACTED
                continue
            entry[k] = _redact(v)

        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str, ensure_ascii=False)


# Standard LogRecord attributes — excluded from extras dump.
_RESERVED = frozenset(
    {
        "name", "msg", "args", "created", "relativeCreated",
        "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "filename", "module", "levelname", "levelno", "pathname",
        "process", "processName", "thread", "threadName", "msecs",
        "message", "taskName",
    }
)


# =============================================================================
# CONFIGURATION
# =============================================================================

_configured = False


def configure_output(stream: Any = None) -> None:
    """Configure (or reconfigure) the logging sinks.

    Default sink: stderr (Docker captures stderr to `docker logs`; stdout
    is reserved for the script-mode JSON result).

    If LOG_FILE env var is set, ALSO writes to that file with rotation
    (10MB x 5 backups). Per 06-logging.md "always stderr AND a rotating
    file" — in containers, the rotating file is opt-in via LOG_FILE so
    users without a writable mount don't get errors.

    Pass stream=sys.stdout to redirect (e.g. when uvicorn captures stderr
    and we want logs visible to a console runner).
    """
    if stream is None:
        stream = sys.stderr

    handlers: list[logging.Handler] = []

    stream_handler = logging.StreamHandler(stream)
    stream_handler.setFormatter(_JSONFormatter())
    handlers.append(stream_handler)

    log_file = os.environ.get("LOG_FILE", "").strip()
    if log_file:
        try:
            rot = RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            rot.setFormatter(_JSONFormatter())
            handlers.append(rot)
        except OSError as e:
            # Don't break boot if the path isn't writable — log to stderr.
            stream_handler.handle(
                logging.LogRecord(
                    name=__name__,
                    level=logging.WARNING,
                    pathname=__file__,
                    lineno=0,
                    msg=f"LOG_FILE={log_file!r} unwritable, falling back to stderr only: {e}",
                    args=(),
                    exc_info=None,
                )
            )

    logging.root.handlers = handlers

    level_name = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO
    logging.root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Get a logger that emits JSON per the project's logging standard."""
    global _configured
    if not _configured:
        configure_output()
        _configured = True
    return logging.getLogger(name)
