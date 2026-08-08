"""The standard config surface and the Settings page.

``GET /config``, ``PUT /config``, ``GET /config/versions`` and
``POST /config/rollback`` are the surface every deployable component exposes
(robotsix-standards ``config-ownership.md``).  The Settings page mounts the
fleet's shared panel from ``@robotsix/ui`` against that surface rather than
rendering a form of its own — no component-specific settings UI exists here,
which is what keeps this UI, the deploy UI and every future one identical.

Account creation and deletion stay separate flows: they validate a mailbox
connection, which is more than a config write.
"""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

from robotsix_auto_mail.server._constants import _update_handler_factory_cache

logger = logging.getLogger(__name__)

#: Cap on a config request body — the panel sends only changed keys.
_MAX_BODY_BYTES = 1_000_000

_SETTINGS_CSS = (Path(__file__).parent / "static" / "settings-panel.css").read_text()

_SETTINGS_PAGE = (
    """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Settings</title>
<link rel="stylesheet" href="/static/robotsix-ui.css">
<style>
"""
    + _SETTINGS_CSS
    + """</style>
</head>
<body>
<main>
<div class="nav-links">
  <a href="/board">&larr; Back to Board</a>
</div>
<div id="settings-panel"></div>
<noscript><p class="panel-fallback">Settings require JavaScript.</p></noscript>
</main>
<script type="module">
  // A missing vendored asset must say so rather than leave a blank page.
  import("/static/robotsix-ui.js")
    .then((ui) => {
      ui.mountConfigPanel(document.getElementById("settings-panel"), {
        title: "Settings",
      });
    })
    .catch(() => {
      document.getElementById("settings-panel").innerHTML =
        '<p class="panel-fallback">The shared config panel asset is missing. ' +
        "It is vendored at image build time; for a local checkout run " +
        "<code>scripts/vendor-ui.sh</code>.</p>";
    });
</script>
</body>
</html>
"""
)


class _SettingsMixin:
    """Mixin providing the standard config surface, the Settings page,
    and account deletion."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

    # -- request helpers -----------------------------------------------------

    def _read_json_body(self) -> dict[str, Any] | None:
        """Parse the body as a JSON object, or answer and return ``None``."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            self._config_problem("empty request body", status=400)
            return None
        if length > _MAX_BODY_BYTES:
            self._config_problem("request body too large", status=413)
            return None
        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            self._config_problem(f"invalid JSON: {exc}", status=400)
            return None
        if not isinstance(body, dict):
            self._config_problem("expected a JSON object", status=400)
            return None
        return body

    def _config_problem(self, detail: str, status: int = 422) -> None:
        """Answer with the fleet's standard error envelope."""
        self._serve_json(
            {
                "type": "urn:robotsix:error:config-validation",
                "title": "Config validation failed",
                "detail": detail,
                "instance": "/config",
            },
            status=status,
        )

    # -- GET /config ---------------------------------------------------------

    def _handle_get_config(self) -> None:
        """Return the effective config, its schema, and the current version.

        Secrets are masked by the model itself — they are never echoed.
        """
        from robotsix_auto_mail.config.service import get_config

        try:
            self._serve_json(get_config(), status=200)
        except Exception as exc:
            logger.error("Failed to read config: %s", exc)
            self._config_problem(f"failed to read config: {exc}", status=500)

    # -- PUT /config ---------------------------------------------------------

    def _handle_put_config(self) -> None:
        """Apply a partial config update and persist it."""
        from robotsix_auto_mail.config.service import (
            ConfigValidationError,
            update_config,
        )

        body = self._read_json_body()
        if body is None:
            return

        try:
            result = update_config(body)
        except ConfigValidationError as exc:
            self._config_problem(exc.detail)
            return
        except Exception as exc:
            logger.error("Failed to write config: %s", exc)
            self._config_problem(f"failed to write config: {exc}", status=500)
            return

        self._refresh_accounts_cache()
        self._serve_json(result, status=200)

    # -- GET /config/versions ------------------------------------------------

    def _handle_get_config_versions(self) -> None:
        """Return recent config versions, newest first."""
        from robotsix_auto_mail.config.service import list_versions

        try:
            self._serve_json(list_versions(), status=200)
        except Exception as exc:
            logger.error("Failed to read config versions: %s", exc)
            self._config_problem(f"failed to read versions: {exc}", status=500)

    # -- POST /config/rollback -----------------------------------------------

    def _handle_config_rollback(self) -> None:
        """Restore a previous version as a new version."""
        from robotsix_auto_mail.config.service import ConfigValidationError, rollback

        body = self._read_json_body()
        if body is None:
            return
        version = body.get("version")
        if not isinstance(version, int) or isinstance(version, bool):
            self._config_problem("'version' must be an integer")
            return

        try:
            result = rollback(version)
        except ConfigValidationError as exc:
            self._config_problem(exc.detail)
            return
        except Exception as exc:
            logger.error("Failed to roll back config: %s", exc)
            self._config_problem(f"failed to roll back: {exc}", status=500)
            return

        self._refresh_accounts_cache()
        self._serve_json(result, status=200)

    # -- shared --------------------------------------------------------------

    def _refresh_accounts_cache(self) -> None:
        """Re-read accounts into the handler factory after a config write.

        The server keeps the loaded accounts in the handler factory's
        keywords; without this the running process would keep serving the
        pre-write config until it is restarted.
        """
        from robotsix_auto_mail.config import load_accounts

        try:
            accounts = load_accounts()
        except Exception:
            logger.warning(
                "Could not reload accounts after a config write", exc_info=True
            )
            return

        handler_factory = getattr(self.server, "RequestHandlerClass", None)
        keywords = getattr(handler_factory, "keywords", None)
        if isinstance(keywords, dict) and "accounts" in keywords:
            keywords["accounts"] = accounts

        self._mirror_to_settings_stores(accounts)

    def _mirror_to_settings_stores(self, accounts: Any) -> None:
        """Refresh each account's settings store from the saved config.

        The per-account store is the recovery path used when the deploy system
        overwrites ``config/config.json`` (it re-adds accounts the config no
        longer lists).  Mirroring every write keeps that snapshot current
        instead of frozen at whatever the account was created with.

        Best-effort: a failure here must never fail the config write.
        """
        from robotsix_auto_mail.server._constants import _with_db
        from robotsix_auto_mail.settings import SettingsStore

        for account in getattr(accounts, "accounts", []):
            db_path = account.config.db_path
            if not db_path:
                continue
            try:
                with _with_db(db_path) as conn:
                    SettingsStore(db_path).seed_from_mail_config(conn, account.config)
            except Exception:
                logger.warning(
                    "Could not mirror config to the settings store for %r",
                    account.account_id,
                    exc_info=True,
                )

    # -- GET /settings-panel -------------------------------------------------

    def _serve_settings_panel(self) -> None:
        """Serve the Settings page, which mounts the shared config panel."""
        self._send_response(_SETTINGS_PAGE, content_type="text/html; charset=utf-8")

    # -- POST /delete-account ------------------------------------------------

    def _handle_delete_account(self) -> None:
        """Delete an account from the persisted configuration.

        POST /delete-account  (form body: ``account_id=<id>``)

        On success returns ``{"ok": true}``.  On failure returns
        ``{"ok": false, "error": "…"}`` with an appropriate status code.
        The handler also updates the handler factory's cached accounts
        and the account list so the change is visible immediately.
        """
        from robotsix_auto_mail.config import (
            load_accounts,
            save_accounts,
        )

        # Read URL-encoded form body.
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            self._serve_json({"ok": False, "error": "empty request body"}, status=400)
            return

        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        body = parse_qs(raw)
        account_id_vals = body.get("account_id", [])
        if not account_id_vals:
            self._serve_json({"ok": False, "error": "missing account_id"}, status=400)
            return
        account_id = account_id_vals[0].strip()
        account_id = account_id.replace("\n", "").replace("\r", "")

        # Load existing config.
        try:
            existing = load_accounts()
        except Exception as exc:
            self._serve_json(
                {"ok": False, "error": f"failed to load config: {exc}"},
                status=500,
            )
            return

        if existing is None or account_id not in existing.ids():
            self._serve_json(
                {"ok": False, "error": f"unknown account: {account_id!r}"},
                status=404,
            )
            return

        # Remove the account.
        new_accounts = [a for a in existing.accounts if a.account_id != account_id]

        try:
            new_config = existing.with_accounts(new_accounts)
        except Exception as exc:
            self._serve_json(
                {"ok": False, "error": f"invalid config after deletion: {exc}"},
                status=500,
            )
            return

        try:
            save_accounts(new_config)
        except Exception as exc:
            logger.error("Failed to save config after deleting account: %s", exc)
            self._serve_json(
                {"ok": False, "error": f"failed to save config: {exc}"},
                status=500,
            )
            return

        # Sanitize account_id before logging: strip newlines to prevent log
        # forgery (CodeQL py/log-injection).
        safe_account_id = account_id.replace("\n", "\\n").replace("\r", "\\r")
        logger.info("Deleted account %r via the settings page", safe_account_id)

        # Update handler factory cache.
        _update_handler_factory_cache(self.server, new_config)

        self._serve_json({"ok": True}, status=200)
