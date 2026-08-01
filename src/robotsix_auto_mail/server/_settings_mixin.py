"""GET /settings, PUT /settings, settings panel, and delete-account mixin."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import html
import json
import logging
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from robotsix_auto_mail.server._constants import _with_db

logger = logging.getLogger(__name__)

# -- Settings panel inline CSS ----------------------------------------------

_SETTINGS_PANEL_CSS = """\
body {
  background: var(--color-bg-page, #121626);
  color: var(--color-text-primary, #eee);
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  max-width: 640px;
  margin: 2rem auto;
  padding: 0 1rem;
}
h1 { margin-bottom: 1.5rem; }
h2 { margin-top: 2rem; margin-bottom: 0.75rem; font-size: 1.1rem; }
.account-list {
  list-style: none;
  padding: 0;
  margin: 0 0 1rem;
}
.account-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.6rem 0.75rem;
  margin-bottom: 0.35rem;
  background: var(--color-bg-panel, #16213e);
  border: 1px solid var(--color-border-button, #3a3a6a);
  border-radius: 4px;
}
.account-item-info {
  flex: 1;
}
.account-item-label {
  font-weight: 600;
  font-size: 0.95rem;
}
.account-item-id {
  font-size: 0.8rem;
  color: var(--color-text-muted, #c0c0e0);
  margin-left: 0.4rem;
}
.account-item-detail {
  font-size: 0.75rem;
  color: var(--color-text-subtle, #a0a0c0);
  margin-top: 0.15rem;
}
.delete-account-btn {
  background: var(--color-bg-danger, #d32f2f);
  color: var(--color-text-on-danger, #fff);
  border: none;
  padding: 0.3rem 0.8rem;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.8rem;
  white-space: nowrap;
}
.delete-account-btn:hover {
  background: var(--color-bg-danger-hover, #b71c1c);
}
.delete-account-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.empty-message {
  color: var(--color-text-muted, #c0c0e0);
  font-style: italic;
  padding: 0.5rem 0;
}
.nav-links {
  margin-top: 2rem;
  display: flex;
  gap: 1.5rem;
}
.nav-links a {
  color: var(--color-text-link, #a0c0ff);
  text-decoration: none;
  font-size: 0.9rem;
}
.nav-links a:hover {
  text-decoration: underline;
}
.error-banner {
  background: var(--color-bg-health, #fde8e8);
  border: 2px solid var(--color-border-health, #d93025);
  border-radius: 4px;
  color: var(--color-text-health, #b71c1c);
  padding: 0.75em 1em;
  margin-bottom: 1.5em;
  font-weight: bold;
}
.success-banner {
  background: var(--color-bg-success-muted, #e8f5e9);
  border: 2px solid var(--color-bg-success, #2e7d32);
  border-radius: 4px;
  color: var(--color-bg-success, #2e7d32);
  padding: 0.75em 1em;
  margin-bottom: 1.5em;
  font-weight: bold;
}
"""


class _SettingsMixin:
    """Mixin providing per-component settings read/write endpoints,
    a settings-panel HTML page, and account deletion."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

    # -- GET /settings -------------------------------------------------------

    def _handle_get_settings(self) -> None:
        """Return all component settings as JSON with secrets masked.

        GET /settings → ``{"settings": {"imap_host": "…", "password": "***", …}}``

        The response includes a ``source`` field indicating whether the
        settings came from the internal store (``"internal"``) or were
        derived from the config file (``"config-file"`` — store was
        empty, no import has run yet).
        """
        from robotsix_auto_mail.settings import SettingsStore

        store = SettingsStore(self.db_path)
        with _with_db(self.db_path) as conn:
            if store.is_empty(conn):
                # No imported settings yet — derive from the in-memory
                # MailConfig (config file) with secrets masked.
                if self.mail_config is not None:
                    cfg = self.mail_config
                    # We can't iterate fields directly on the Protocol type,
                    # so use the concrete MailConfig fields.
                    from robotsix_auto_mail.config.model import MailConfig
                    from robotsix_auto_mail.settings.store import _masked_value

                    settings = {
                        field_name: _masked_value(
                            field_name,
                            str(getattr(cfg, field_name)),
                        )
                        for field_name in MailConfig.model_fields
                    }
                else:
                    settings = {}
                self._serve_json(
                    {"settings": settings, "source": "config-file"}, status=200
                )
                return

            settings = store.get_all(conn)

        self._serve_json({"settings": settings, "source": "internal"}, status=200)

    # -- PUT /settings -------------------------------------------------------

    def _handle_put_settings(self) -> None:
        """Validate and apply partial settings updates.

        PUT /settings  (JSON body: ``{"imap_host": "new.example.com", …}``)

        Returns ``{"ok": true, "errors": {}}`` on success, or
        ``{"ok": false, "errors": {"bad_field": "error message", …}}``
        on validation failure.  On partial failure valid fields are
        still persisted (the ``errors`` dict lists only the rejected
        keys).
        """
        from robotsix_auto_mail.settings import SettingsStore

        # Read the raw request body.
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            self._serve_json(
                {"ok": False, "errors": {"_body": "empty request body"}},
                status=400,
            )
            return

        raw_body = self.rfile.read(length)
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            self._serve_json(
                {"ok": False, "errors": {"_body": f"invalid JSON: {exc}"}},
                status=400,
            )
            return

        if not isinstance(body, dict):
            self._serve_json(
                {"ok": False, "errors": {"_body": "expected a JSON object"}},
                status=400,
            )
            return

        store = SettingsStore(self.db_path)
        with _with_db(self.db_path) as conn:
            errors = store.update(conn, body)

        if errors:
            self._serve_json({"ok": False, "errors": errors}, status=422)
            return

        self._serve_json({"ok": True, "errors": {}}, status=200)

    # -- GET /settings-panel -------------------------------------------------

    def _serve_settings_panel(
        self,
        error: str = "",
        success: str = "",
    ) -> None:
        """Serve the settings-panel HTML page listing accounts with delete buttons.

        GET /settings-panel → HTML page

        When *error* or *success* is non-empty, a banner is rendered at
        the top of the page (used after a failed or successful deletion).
        """
        from robotsix_auto_mail.config import load_accounts

        try:
            accounts_cfg = load_accounts()
        except Exception:
            accounts_cfg = None

        # Build the account list HTML.
        account_items_html = ""
        if accounts_cfg is not None and accounts_cfg.ids():
            for account in accounts_cfg.accounts:
                label = (
                    html.escape(account.label)
                    if account.label
                    else html.escape(account.account_id)
                )
                account_id = html.escape(account.account_id)
                imap_host = html.escape(account.config.imap_host)
                username = html.escape(account.config.username)
                default_badge = ""
                if account.account_id == accounts_cfg.default_account_id:
                    default_badge = ' <span class="account-item-id">(default)</span>'
                account_items_html += (
                    '<li class="account-item"'
                    f' id="account-{account_id}">\n'
                    '  <div class="account-item-info">\n'
                    f'   <span class="account-item-label">{label}'
                    f"{default_badge}</span>\n"
                    f'   <div class="account-item-detail">'
                    f"{username} @ {imap_host}</div>\n"
                    "  </div>\n"
                    f'  <button class="delete-account-btn"'
                    f' data-account-id="{account_id}"'
                    f' onclick="deleteAccount(this)">Delete</button>\n'
                    "</li>\n"
                )
        else:
            account_items_html = (
                '<li class="empty-message">No accounts configured.</li>\n'
            )

        banner_html = ""
        if error:
            banner_html = f'<div class="error-banner">{html.escape(error)}</div>\n'
        elif success:
            banner_html = f'<div class="success-banner">{html.escape(success)}</div>\n'

        body = (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '<meta charset="utf-8">\n'
            "<title>Settings</title>\n"
            f"<style>{_SETTINGS_PANEL_CSS}</style>\n"
            "</head>\n"
            "<body>\n"
            "<h1>Settings</h1>\n"
            f"{banner_html}"
            "<h2>Mail Accounts</h2>\n"
            '<ul class="account-list" id="account-list">\n'
            f"{account_items_html}"
            "</ul>\n"
            '<div class="nav-links">\n'
            '<a href="/add-account">+ Add Account</a>\n'
            '<a href="/board">← Back to Board</a>\n'
            "</div>\n"
            "<script>\n"
            "function deleteAccount(btn) {\n"
            "  var accountId = btn.getAttribute('data-account-id');\n"
            "  if (!confirm('Delete account \\'' + accountId +"
            " '\\'?\\n\\nThis will remove the account from the"
            " configuration file and cannot be undone.')) return;\n"
            "  btn.disabled = true;\n"
            "  btn.textContent = 'Deleting…';\n"
            "  fetch('/delete-account', {\n"
            "    method: 'POST',\n"
            "    headers: { 'Content-Type':"
            " 'application/x-www-form-urlencoded' },\n"
            "    body: 'account_id=' + encodeURIComponent(accountId)\n"
            "  })\n"
            "  .then(function(r) {\n"
            "    if (r.redirected) {\n"
            "      window.location.href = r.url;\n"
            "      return;\n"
            "    }\n"
            "    return r.json();\n"
            "  })\n"
            "  .then(function(data) {\n"
            "    if (data && data.ok) {\n"
            "      var item = document.getElementById("
            "'account-' + CSS.escape(accountId));\n"
            "      if (item) item.remove();\n"
            "      var list = document.getElementById('account-list');\n"
            "      if (list && !list.querySelector('.account-item')) {\n"
            '        list.innerHTML = \'<li class="empty-message">'
            "No accounts configured.</li>';\n"
            "      }\n"
            "    } else {\n"
            "      btn.disabled = false;\n"
            "      btn.textContent = 'Delete';\n"
            "      alert((data && data.error) || 'Delete failed.');\n"
            "    }\n"
            "  })\n"
            "  .catch(function() {\n"
            "    btn.disabled = false;\n"
            "    btn.textContent = 'Delete';\n"
            "    alert('Network error — please try again.');\n"
            "  });\n"
            "}\n"
            "</script>\n"
            "</body>\n"
            "</html>"
        )

        self._send_response(body, content_type="text/html; charset=utf-8")

    # -- POST /delete-account ------------------------------------------------

    def _handle_delete_account(self) -> None:
        """Delete an account from the persisted configuration.

        POST /delete-account  (form body: ``account_id=<id>``)

        On success returns ``{"ok": true}``.  On failure returns
        ``{"ok": false, "error": "…"}`` with an appropriate status code.
        The handler also updates the handler factory's cached accounts
        and default_account_id so the change is visible immediately.
        """
        from robotsix_auto_mail.config import (
            MailAccountsConfig,
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

        # Pick a new default if the deleted account was the default.
        default_id = existing.default_account_id
        if account_id == default_id:
            default_id = new_accounts[0].account_id if new_accounts else ""

        try:
            new_config = MailAccountsConfig(
                accounts=new_accounts,
                default_account_id=default_id,
            )
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

        logger.info("Deleted account %r via settings panel", account_id)

        # Update handler factory cache.
        handler_factory = getattr(self.server, "RequestHandlerClass", None)
        if handler_factory is not None and hasattr(handler_factory, "keywords"):
            kw = handler_factory.keywords
            if "accounts" in kw:
                kw["accounts"] = new_config
            if "default_account_id" in kw:
                kw["default_account_id"] = default_id

        self._serve_json({"ok": True}, status=200)
