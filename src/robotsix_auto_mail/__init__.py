__version__ = "0.0.0"

import importlib.abc
import importlib.machinery
import sys
import types
import warnings

from robotsix_auto_mail._observability import (
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

# ---------------------------------------------------------------------------
# Deprecation shim: robotsix_auto_mail.observability → direct top-level names
# ---------------------------------------------------------------------------

_OBS_DEPRECATED_MSG = (
    "robotsix_auto_mail.observability is deprecated; "
    "use robotsix_auto_mail.setup_observability (and friends) directly."
)

_OBS_FULL_NAME = "robotsix_auto_mail.observability"


class _DeprecatedObservability(types.ModuleType):
    """Proxy module that warns on every attribute access."""

    _OBS_ATTRS = frozenset(
        {"init_langfuse_tracing", "setup_logging", "setup_observability"}
    )

    def __init__(self) -> None:
        super().__init__(_OBS_FULL_NAME)

    def __getattr__(self, name: str) -> object:
        if name == "__all__":
            return sorted(self._OBS_ATTRS)
        if name in self._OBS_ATTRS:
            warnings.warn(
                f"{_OBS_FULL_NAME}.{name} is deprecated; "
                f"use robotsix_auto_mail.{name} instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            return globals()[name]
        raise AttributeError(f"module '{_OBS_FULL_NAME}' has no attribute {name!r}")


class _ObservabilityLoader(importlib.abc.Loader):
    """Loader that returns the pre-created deprecation module."""

    def create_module(self, spec: object) -> types.ModuleType:
        return sys.modules.get(_OBS_FULL_NAME, _DeprecatedObservability())

    def exec_module(self, module: types.ModuleType) -> None:
        pass


class _ObservabilityFinder:
    """Meta-path finder that intercepts imports of the deprecated submodule."""

    @staticmethod
    def find_spec(
        fullname: str, path: object, target: object = None
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname != _OBS_FULL_NAME:
            return None
        warnings.warn(_OBS_DEPRECATED_MSG, DeprecationWarning, stacklevel=2)
        mod = _DeprecatedObservability()
        sys.modules[_OBS_FULL_NAME] = mod
        spec = importlib.machinery.ModuleSpec(
            _OBS_FULL_NAME, _ObservabilityLoader(), is_package=False
        )
        return spec


sys.meta_path.insert(0, _ObservabilityFinder)


def __getattr__(name: str) -> object:
    if name == "observability":
        warnings.warn(_OBS_DEPRECATED_MSG, DeprecationWarning, stacklevel=2)
        return sys.modules.get(_OBS_FULL_NAME, _DeprecatedObservability())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
