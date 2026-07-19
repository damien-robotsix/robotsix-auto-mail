"""Observability setup for robotsix-auto-mail: logging + Langfuse tracing.

Delegates the core logging pipeline to
:func:`robotsix_llmio.logging.setup_logging` (stream handler, formatter,
OTel trace-id injection) and Langfuse tracing to
:func:`robotsix_llmio.core.setup_langfuse_tracing`.

Call :func:`setup_observability` once at startup, optionally passing a
loaded :class:`~robotsix_auto_mail.config.MailConfig`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from robotsix_llmio.core import install_signal_handlers, setup_langfuse_tracing
from robotsix_llmio.logging import (
    setup_logging as _llmio_setup_logging,
)

if TYPE_CHECKING:
    from robotsix_auto_mail.config import MailConfig


def setup_logging(
    *,
    level: str = "INFO",
    log_format: str = "console",
) -> None:
    """Configure logging with OTel trace-id injection.

    Delegates stream-handler setup to :func:`robotsix_llmio.logging.setup_logging`.

    Args:
        level: Log level name for the console stream (``DEBUG`` / ``INFO`` /
            ``WARNING`` / ``ERROR``; default ``INFO``).
        log_format: ``"console"`` (the default) for human-readable output or
            ``"json"`` for structured production logs.

    Safe to call once per process (idempotent).
    """
    _llmio_setup_logging(
        level=level,
        fmt=log_format,
        loggers=["robotsix_auto_mail"],
    )


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

    Configures the console logging pipeline and (when Langfuse
    credentials are available) the OTel tracing provider.  Both
    sub-systems are safe to call more than once (idempotent).

    Args:
        config: An optional :class:`MailConfig`.  When given, its
            ``log_level`` and ``log_format`` fields control logging
            verbosity and output, and its ``langfuse_public_key`` /
            ``langfuse_secret_key`` / ``langfuse_base_url`` fields
            drive Langfuse tracing.  When omitted or ``None``,
            defaults are used for logging and tracing falls back to
            environment variables.
    """
    if config is not None:
        setup_logging(
            level=config.log_level,
            log_format=config.log_format,
        )
    else:
        setup_logging()

    init_langfuse_tracing(config)
