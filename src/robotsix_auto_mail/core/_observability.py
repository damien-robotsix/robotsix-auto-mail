"""Observability setup for robotsix-auto-mail: logging + Langfuse tracing.

Delegates the core logging pipeline to
:func:`robotsix_llmio.logging.setup_logging` (stream handler, formatter,
OTel trace-id injection) and Langfuse tracing to
:func:`robotsix_llmio.core.setup_langfuse_tracing`.

Call :func:`setup_observability` once at startup, optionally passing a
loaded :class:`~robotsix_auto_mail.config.MailConfig`.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from robotsix_llmio.core import install_signal_handlers, setup_langfuse_tracing
from robotsix_llmio.logging import (
    setup_logging as _llmio_setup_logging,
)

from robotsix_auto_mail.config.credentials import MAIN_LLM_ALIAS

if TYPE_CHECKING:
    from robotsix_auto_mail.config import LangfuseConfig, MailConfig

logger = logging.getLogger(__name__)

# Langfuse Cloud — used when the canonical block leaves ``host`` empty, so
# the base URL never comes from the ``LANGFUSE_BASE_URL`` env var.
_DEFAULT_LANGFUSE_HOST = "https://cloud.langfuse.com"


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
        # stderr, not llmio's stdout default: CLI subcommands print JSON
        # payloads on stdout, which log lines must never corrupt.
        stream=sys.stderr,
    )


def init_langfuse_tracing(langfuse: LangfuseConfig | None = None) -> bool:
    """Enable Langfuse tracing from the canonical ``langfuse`` block — only.

    *langfuse* is the block declared in ``config/config.json``; the
    project traced is the one aliased :data:`MAIN_LLM_ALIAS`, auto-mail's
    single LLM function.  The canonical block is the **only** credential
    source (robotsix-standards ``component-standard.md``, "LLM tracing
    credentials — no fallback"): when it is absent, or the project is
    missing or half-filled, tracing is disabled explicitly with a one-line
    notice and llmio is never called, so its ``LANGFUSE_*`` env-var
    fallback can never silently pick up deploy-plane credentials.  An
    unset ``host`` resolves to Langfuse Cloud here rather than to the
    ``LANGFUSE_BASE_URL`` env var.

    Returns:
        ``True`` if tracing was successfully set up, ``False`` if the
        canonical block is unconfigured or setup failed (application
        should continue normally either way).
    """
    project = langfuse.project() if langfuse else None
    if langfuse is None or project is None or not project.is_configured():
        logger.info(
            "Langfuse tracing disabled: the canonical 'langfuse' block has no "
            "configured %r project",
            MAIN_LLM_ALIAS,
        )
        return False
    ok: bool = setup_langfuse_tracing(
        service_name="robotsix-auto-mail",
        public_key=project.public_key,
        secret_key=project.secret_key.get_secret_value(),
        base_url=langfuse.host or _DEFAULT_LANGFUSE_HOST,
    )
    if ok:
        install_signal_handlers()
    return ok


def setup_observability(
    config: MailConfig | None = None,
) -> None:
    """Set up logging + Langfuse tracing.

    Configures the console logging pipeline and (when Langfuse
    credentials are available) the OTel tracing provider.  Both
    sub-systems are safe to call more than once (idempotent).

    Args:
        config: An optional :class:`MailConfig` whose ``log_level`` and
            ``log_format`` fields control logging verbosity and output.
            When omitted or ``None``, logging defaults are used.

    Tracing credentials are not taken from *config*: they belong to the
    component rather than to a mailbox, so they are read from the
    canonical ``langfuse`` block via
    :func:`~robotsix_auto_mail.config.load_langfuse` — which means
    tracing is configured identically no matter which account (if any)
    the caller happens to hold.
    """
    if config is not None:
        setup_logging(
            level=config.log_level,
            log_format=config.log_format,
        )
    else:
        setup_logging()

    from robotsix_auto_mail.config import load_langfuse

    init_langfuse_tracing(load_langfuse())
