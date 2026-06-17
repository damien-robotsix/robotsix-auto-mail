"""Langfuse tracing initialisation via robotsix_llmio's OTel layer.

Call :func:`init_langfuse_tracing` once at startup, optionally passing a
loaded :class:`~robotsix_auto_mail.config.MailConfig`.  When a config is
given, its ``langfuse_public_key`` / ``langfuse_secret_key`` /
``langfuse_base_url`` fields are forwarded to
:func:`robotsix_llmio.core.setup_langfuse_tracing`; empty fields (and a
``None`` config) fall back to llmio's own ``LANGFUSE_*`` environment
behaviour.  When no credentials resolve from any source the call is a
silent no-op.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from robotsix_llmio.core import install_signal_handlers, setup_langfuse_tracing

if TYPE_CHECKING:
    from robotsix_auto_mail.config import MailConfig


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
    secret_key = (config.langfuse_secret_key or None) if config else None
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
