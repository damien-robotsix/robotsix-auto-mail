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
    from robotsix_auto_mail.config import LangfuseConfig, MailConfig


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


def init_langfuse_tracing(langfuse: LangfuseConfig | None = None) -> bool:
    """Enable Langfuse tracing from the canonical ``langfuse`` block.

    *langfuse* is the block declared in ``config/config.json``; the
    project traced is the one aliased :data:`MAIN_LLM_ALIAS`, auto-mail's
    single LLM function.  Unset fields convert to ``None`` so llmio falls
    back to the ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` /
    ``LANGFUSE_BASE_URL`` env vars, and ``langfuse=None`` reproduces the
    env-only behaviour.

    Returns:
        ``True`` if tracing was successfully set up, ``False`` if
        credentials were missing (application should continue normally
        either way).
    """
    project = langfuse.project() if langfuse else None
    # A half-filled project is unconfigured, not broken: passing one key on
    # its own would hand llmio a credential pair it cannot use, instead of
    # falling through to the env vars.
    if project is not None and not project.is_configured():
        project = None
    public_key = project.public_key if project else None
    secret_key = project.secret_key.get_secret_value() if project else None
    base_url = (langfuse.host or None) if langfuse else None
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
