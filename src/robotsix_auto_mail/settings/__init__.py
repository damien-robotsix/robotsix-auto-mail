"""Per-component settings store and one-time import from central-deploy.

Provides a :class:`SettingsStore` backed by the per-account SQLite database
and a one-time import helper that seeds it from central-deploy's export
endpoint on first boot.  Also provides :func:`discover_accounts_from_settings_stores`
for recovering accounts from their settings stores when the main config file
has been overwritten.
"""

from __future__ import annotations

from robotsix_auto_mail.settings.import_ import import_from_central_deploy
from robotsix_auto_mail.settings.store import SettingsStore
from robotsix_auto_mail.settings.store import discover_accounts_from_settings_stores

__all__ = [
    "SettingsStore",
    "discover_accounts_from_settings_stores",
    "import_from_central_deploy",
]
