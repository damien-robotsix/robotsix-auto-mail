"""Observability setup for robotsix-auto-mail: logging + Langfuse tracing.

Delegates the core logging pipeline to
:func:`robotsix_llmio.logging.setup_logging` (stream handler, formatter,
OTel trace-id injection) and Langfuse tracing to
:func:`robotsix_llmio.core.setup_langfuse_tracing`.

Call :func:`setup_observability` once at startup, optionally passing a
loaded :class:`~robotsix_auto_mail.config.MailConfig`.
"""

from __future__ import annotations

import datetime
import logging
import os
import sys
from typing import TYPE_CHECKING

from robotsix_llmio.core import install_signal_handlers, setup_langfuse_tracing
from robotsix_llmio.logging import (
    OTelTraceFilter,
)
from robotsix_llmio.logging import (
    setup_logging as _llmio_setup_logging,
)

if TYPE_CHECKING:
    from robotsix_auto_mail.config import MailConfig


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


def init_langfuse_tracing(config: MailConfig | None = None) -> bool:
    """Enable Langfuse tracing from *config* (with env fallback).

    When *config* is provided, its ``langfuse_public_key``,
    ``langfuse_secret_key`` and ``langfuse_base_url`` fields are passed
    to :func:`setup_langfuse_tracing`.  Empty-string fields convert to
    ``None`` so llmio falls back to the ``LANGFUSE_PUBLIC_KEY`` /
    ``LANGFUSE_SECRET_KEY`` / ``LANGFUSE_BASE_URL`` env vars exactly as
    before.  Passing ``config=None`` reproduces the previous
    env-only behaviour.

    Returns:
        ``True`` if tracing was successfully set up, ``False`` if
        credentials were missing (application should continue normally
        either way).
    """
    public_key = (config.langfuse_public_key or None) if config else None
    secret_key = (
        (config.langfuse_secret_key.get_secret_value() or None) if config else None
    )
    base_url = (config.langfuse_base_url or None) if config else None
    ok: bool = setup_langfuse_tracing(
        service_name="robotsix-auto-mail",
        public_key=public_key,
        secret_key=secret_key,
        base_url=base_url,
    )
    if ok:
        install_signal_handlers()
    return ok


def setup_observability(
    config: MailConfig | None = None,
) -> None:
    """Set up logging + Langfuse tracing from *config*.

    Configures the console/file logging pipeline and (when Langfuse
    credentials are available) the OTel tracing provider.  Both
    sub-systems are safe to call more than once (idempotent).

    Args:
        config: An optional :class:`MailConfig`.  When given, its
            ``log_level``, ``log_format`` and ``log_file_dir``
            fields control logging verbosity and output, and its
            ``langfuse_public_key`` / ``langfuse_secret_key`` /
            ``langfuse_base_url`` fields drive Langfuse tracing.
            When omitted or ``None``, defaults are used for logging
            and tracing falls back to environment variables.
    """
    if config is not None:
        setup_logging(
            level=config.log_level,
            log_format=config.log_format,
            log_file_dir=config.log_file_dir,
        )
    else:
        setup_logging()

    init_langfuse_tracing(config)
