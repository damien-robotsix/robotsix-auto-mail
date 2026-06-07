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

import logging
import os
import sys

import structlog
from structlog.typing import Processor


def _resolve_level() -> int:
    """Resolve the numeric log level from ``LOG_LEVEL`` (default ``INFO``).

    Accepts ``DEBUG`` / ``INFO`` / ``WARNING`` / ``ERROR`` case-insensitively;
    an unrecognised value falls back to ``INFO``.
    """
    name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, name, None)
    if not isinstance(level, int):
        return logging.INFO
    return level


def setup_logging() -> None:
    """Configure structlog + stdlib logging from the environment.

    Reads ``LOG_LEVEL`` (default ``INFO``) and ``LOG_FORMAT`` (default
    ``console``; ``json`` selects the JSON renderer, anything else the dev
    console renderer).  Safe to call once per process (idempotent).
    """
    level = _resolve_level()
    log_format = os.environ.get("LOG_FORMAT", "console").lower()

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
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=False,
    )

    # Bridge stdlib logging so third-party libraries render through the same
    # pipeline; set the root logger level from LOG_LEVEL.
    logging.basicConfig(format="%(message)s", level=level, stream=sys.stdout)
    logging.getLogger().setLevel(level)
