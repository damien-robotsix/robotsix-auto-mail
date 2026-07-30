"""Per-component settings store and one-time import from central-deploy.

Provides a :class:`SettingsStore` backed by the per-account SQLite database
and a one-time import helper that seeds it from central-deploy's export
endpoint on first boot.
"""

from __future__ import annotations

from robotsix_auto_mail.settings.import_ import import_from_central_deploy
from robotsix_auto_mail.settings.store import SettingsStore

__all__ = [
    "SettingsStore",
    "import_from_central_deploy",
]
