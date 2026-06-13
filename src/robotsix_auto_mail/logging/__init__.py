"""Central logging configuration for robotsix-auto-mail.

Exposes a single :func:`setup_logging` entry point built on ``structlog``.
It is env-driven — ``LOG_LEVEL`` controls verbosity and ``LOG_FORMAT``
selects between a machine-readable JSON renderer (for production / log
aggregation) and a human-friendly console renderer (for development).

stdlib logging is bridged into the same pipeline via
``structlog.stdlib.LoggerFactory`` and ``logging.basicConfig`` so that
third-party libraries emitting through the standard library flow through
the same renderer.
"""

from __future__ import annotations

import datetime
import logging
import os
import sys

import structlog
from structlog.typing import Processor


def _resolve_level(level_name: str = "INFO") -> int:
    """Resolve a numeric log level from *level_name* (default ``INFO``).

    Accepts ``DEBUG`` / ``INFO`` / ``WARNING`` / ``ERROR`` case-insensitively;
    an unrecognised value falls back to ``INFO``.
    """
    name = level_name.upper()
    level = getattr(logging, name, None)
    if not isinstance(level, int):
        return logging.INFO
    return level


def setup_logging(
    *,
    level: str = "INFO",
    log_format: str = "console",
    log_file_dir: str = ".mail_log",
) -> None:
    """Configure structlog + stdlib logging.

    *level* is the log level name (``DEBUG`` / ``INFO`` / ``WARNING``
    / ``ERROR``; default ``INFO``).  *log_format* selects the renderer:
    ``"json"`` for structured production logs, ``"console"`` (the default)
    for human-friendly development output.  *log_file_dir* is the directory
    for date-stamped debug log files (default ``".mail_log"``); an empty
    or whitespace-only string disables file logging.

    Safe to call once per process (idempotent).
    """
    level_value = _resolve_level(level)
    log_format = log_format.lower()

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    processors: list[Processor]
    if log_format == "json":
        processors = [
            *shared_processors,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(),
        ]

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        cache_logger_on_first_use=False,
    )

    # Bridge stdlib logging so third-party libraries render through the same
    # pipeline; set the root logger level from LOG_LEVEL.
    logging.basicConfig(format="%(message)s", level=level_value, stream=sys.stdout)
    logging.getLogger().setLevel(level_value)

    # -- file handler --------------------------------------------------------
    # Always DEBUG; survives independently of LOG_LEVEL (which only governs
    # stdout).  Date-stamped filenames give natural daily rollover without
    # a rotation library.
    log_file_dir = log_file_dir.strip()
    if log_file_dir:
        root = logging.getLogger()
        if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
            try:
                os.makedirs(log_file_dir, exist_ok=True)
            except OSError:
                print(
                    f"LOG_FILE_DIR is set to {log_file_dir!r} but the"
                    f" directory could not be created; file logging"
                    f" disabled.",
                    file=sys.stderr,
                )
            else:
                today = datetime.date.today().isoformat()  # YYYY-MM-DD
                log_path = os.path.join(log_file_dir, f"mail-{today}.log")
                file_handler = logging.FileHandler(log_path)
                file_handler.setLevel(logging.DEBUG)
                file_handler.setFormatter(logging.Formatter("%(message)s"))
                root.addHandler(file_handler)
                # Lower the root logger to DEBUG so the file handler
                # receives all events.  The StreamHandler added by
                # basicConfig retains its own level filter (LOG_LEVEL),
                # so stdout stays at the configured verbosity.
                root.setLevel(logging.DEBUG)
