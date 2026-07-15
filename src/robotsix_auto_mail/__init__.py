__version__ = "0.0.0"

from robotsix_auto_mail.core._observability import (
    init_langfuse_tracing,
    setup_logging,
    setup_observability,
)

__all__ = [
    "__version__",
    "init_langfuse_tracing",
    "setup_logging",
    "setup_observability",
]
