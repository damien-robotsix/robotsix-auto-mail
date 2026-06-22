"""Central logging configuration for robotsix-auto-mail.

Exposes a single :func:`setup_logging` entry point that delegates the core
pipeline to :func:`robotsix_llmio.logging.setup_logging` (stream handler,
formatter, OTel trace-id injection) and adds only a date-stamped file
handler on top.

The file handler always logs at ``DEBUG`` level, survives independently of
the configured console level, and writes to ``.mail_log/mail-YYYY-MM-DD.log``
by default.
"""

from __future__ import annotations

import datetime
import logging
import os
import sys

from robotsix_llmio.logging import (
    OTelTraceFilter,
)
from robotsix_llmio.logging import (
    setup_logging as _llmio_setup_logging,
)


def setup_logging(
    *,
    level: str = "INFO",
    log_format: str = "console",
    log_file_dir: str = ".mail_log",
) -> None:
    """Configure logging with OTel trace-id injection + date-stamped file output.

    Delegates stream-handler setup to :func:`robotsix_llmio.logging.setup_logging`
    and then attaches a ``DEBUG``-level :class:`~logging.FileHandler` to the
    ``"robotsix_auto_mail"`` logger for persistent audit trails.

    Args:
        level: Log level name for the console stream (``DEBUG`` / ``INFO`` /
            ``WARNING`` / ``ERROR``; default ``INFO``).
        log_format: ``"console"`` (the default) for human-readable output or
            ``"json"`` for structured production logs.
        log_file_dir: Directory for date-stamped debug log files (default
            ``".mail_log"``). An empty or whitespace-only string disables
            file logging.

    Safe to call once per process (idempotent).
    """
    # Core pipeline: stream handler, formatter, OTel trace-id filter.
    _llmio_setup_logging(
        level=level,
        fmt=log_format,
        loggers=["robotsix_auto_mail"],
    )

    # -- date-stamped file handler ------------------------------------------
    # Always DEBUG; survives independently of *level* (which only governs the
    # console stream).  Date-stamped filenames give natural daily rollover
    # without a rotation library.
    log_file_dir = log_file_dir.strip()
    if not log_file_dir:
        return

    target = logging.getLogger("robotsix_auto_mail")
    if any(isinstance(h, logging.FileHandler) for h in target.handlers):
        return  # already attached — idempotent

    try:
        os.makedirs(log_file_dir, exist_ok=True)
    except OSError:
        print(
            f"log_file_dir is set to {log_file_dir!r} but the"
            f" directory could not be created; file logging disabled.",
            file=sys.stderr,
        )
        return

    today = datetime.date.today().isoformat()  # YYYY-MM-DD
    log_path = os.path.join(log_file_dir, f"mail-{today}.log")
    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(logging.DEBUG)
    file_handler.addFilter(OTelTraceFilter())
    # Reuse the same formatter that llmio attached to the StreamHandler.
    if target.handlers:
        file_handler.setFormatter(target.handlers[0].formatter)

    target.addHandler(file_handler)
    # Lower the logger level so DEBUG records reach the file handler; the
    # StreamHandler retains its own level filter, so the console stays at
    # the configured verbosity.
    target.setLevel(logging.DEBUG)
