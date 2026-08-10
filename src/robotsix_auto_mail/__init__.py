from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version

try:
    __version__ = _package_version("robotsix-auto-mail")
except PackageNotFoundError:
    __version__ = "0.4.0"

from robotsix_auto_mail.core._observability import (
    init_langfuse_tracing,
    setup_logging,
    setup_observability,
)
from robotsix_auto_mail.errors import RobotsixMailError

__all__ = [
    "RobotsixMailError",
    "__version__",
    "init_langfuse_tracing",
    "setup_logging",
    "setup_observability",
]
