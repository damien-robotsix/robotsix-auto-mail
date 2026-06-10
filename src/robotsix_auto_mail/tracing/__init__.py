"""Langfuse tracing initialisation via robotsix_llmio's OTel layer.

Call :func:`init_langfuse_tracing` once at startup; it reads credentials
from the environment and registers them with the OTel tracer provider inside
robotsix_llmio.  When credentials are absent the call is a silent no-op.
"""

from __future__ import annotations

from robotsix_llmio.core import install_signal_handlers, setup_langfuse_tracing


def init_langfuse_tracing() -> bool:
    """Enable Langfuse tracing from environment variables.

    Reads ``LANGFUSE_PUBLIC_KEY``, ``LANGFUSE_SECRET_KEY``, and
    ``LANGFUSE_BASE_URL`` (optional) — :func:`setup_langfuse_tracing`
    already falls back to these env var names internally.

    Returns:
        ``True`` if tracing was successfully set up, ``False`` if
        credentials were missing (application should continue normally
        either way).
    """
    ok = setup_langfuse_tracing(service_name="robotsix-auto-mail")
    if ok:
        install_signal_handlers()
    return ok
