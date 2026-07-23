"""Add-account mixin for the board server — form + handler for creating
a new mail account through the web UI."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import html
import logging
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from robotsix_auto_mail.config import (
    MailAccount,
    MailAccountsConfig,
    MailConfig,
    load_accounts,
    save_accounts,
)
from robotsix_auto_mail.config.schema import (
    _VALID_TLS_MODES,
    DEFAULT_IMAP_TLS_MODE,
    DEFAULT_SMTP_TLS_MODE,
)

if TYPE_CHECKING:
    from ._board_handler_protocol import BoardHandlerProtocol

logger = logging.getLogger(__name__)

# -- Constants ---------------------------------------------------------------
_REQUIRED_FIELDS = ("account_id", "imap_host", "smtp_host", "username", "password")

# Pre-rendered HTML fragments shared by GET /add-account (fresh form) and
# POST /add-account (re-render with error + pre-filled values).
#
# NOTE: The var() fallback colours below are intentional and load-bearing —
# /add-account is a standalone page that does not link board.css, so the
# CSS custom properties are never defined.  The second argument to every
# var() call is the actual colour used at runtime.
_ADD_ACCOUNT_FORM_CSS = """\
body {
  background: var(--color-bg-page, #121626);
  color: var(--color-text-primary, #eee);
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  max-width: 560px;
  margin: 3rem auto;
  padding: 0 1rem;
}
h1 { margin-bottom: 1.5rem; }
label {
  display: block;
  margin-top: 0.75rem;
  font-weight: 600;
  font-size: 0.9rem;
  color: var(--color-text-secondary, #e0e0e0);
}
input, select {
  display: block;
  width: 100%;
  box-sizing: border-box;
  margin-top: 0.25rem;
  padding: 0.5rem;
  font-size: 0.95rem;
  border: 1px solid var(--color-border-button, #3a3a6a);
  border-radius: 4px;
  background: var(--color-bg-panel, #16213e);
  color: var(--color-text-primary, #eee);
}
input:focus, select:focus {
  outline: 1px solid var(--color-text-link, #a0c0ff);
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
.form-actions {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  margin-top: 1.5rem;
}
.form-actions button[type="submit"] {
  width: auto;
  background: var(--color-bg-success, #2e7d32);
  color: var(--color-text-on-success, #fff);
  border: none;
  padding: 0.5rem 1.5rem;
  font-size: 0.95rem;
  font-weight: 600;
  border-radius: 4px;
  cursor: pointer;
}
.form-actions button[type="submit"]:hover {
  background: var(--color-bg-success-hover, #1b5e20);
}
.form-actions .cancel-link {
  color: var(--color-text-muted, #c0c0e0);
  text-decoration: none;
  font-size: 0.9rem;
}
.form-actions .cancel-link:hover {
  text-decoration: underline;
}
details {
  margin-top: 0.75rem;
}
details summary {
  font-weight: 600;
  font-size: 0.85rem;
  color: var(--color-text-muted, #c0c0e0);
  cursor: pointer;
}
details label {
  font-weight: 500;
  font-size: 0.82rem;
}
details input, details select {
  font-size: 0.85rem;
  padding: 0.35rem;
}
"""


class _AccountMixin:
    """Mixin providing the add-account endpoint for the board."""

    if TYPE_CHECKING:
        self: BoardHandlerProtocol

    # -- GET /add-account --------------------------------------------------

    def _serve_add_account(
        self,
        error: str = "",
        prefill: dict[str, str] | None = None,
    ) -> None:
        """Serve the account-creation form (GET) or re-render on error (POST)."""
        p = prefill or {}
        body = _build_add_account_form_html(error=error, prefill=p)
        self._send_response(body, content_type="text/html; charset=utf-8")

    # -- POST /add-account -------------------------------------------------

    def _handle_add_account(self) -> None:
        """Process the account-creation form submission."""
        # 1. Read the URL-encoded POST body.
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        body = parse_qs(raw)

        # 2. Extract form values (first value per key).
        prefill: dict[str, str] = {}
        fields: dict[str, str] = {}
        for key in (
            "account_id",
            "label",
            "imap_host",
            "smtp_host",
            "username",
            "password",
            "imap_port",
            "smtp_port",
            "imap_tls_mode",
            "smtp_tls_mode",
            "imap_folder",
        ):
            vals = body.get(key, [])
            value = vals[0].strip() if vals else ""
            if key != "password":
                prefill[key] = value
            fields[key] = value

        # 3. Validate required fields.
        missing = [f for f in _REQUIRED_FIELDS if not fields.get(f)]
        if missing:
            self._serve_add_account(
                error=f"Missing required fields: {', '.join(missing)}",
                prefill=prefill,
            )
            return

        # 4. Validate account_id charset (pydantic-level check via MailAccount
        #    construction later, but fail-fast with a user-friendly message).
        account_id = fields["account_id"]
        from robotsix_auto_mail.config.model import _ACCOUNT_ID_RE

        if not _ACCOUNT_ID_RE.match(account_id):
            self._serve_add_account(
                error=(
                    f"Account ID '{html.escape(account_id)}' contains"
                    f" invalid characters. Use only letters, digits,"
                    f" dots, underscores, and hyphens."
                ),
                prefill=prefill,
            )
            return

        # 5. Validate TLS mode values.
        imap_tls = fields.get("imap_tls_mode") or DEFAULT_IMAP_TLS_MODE
        smtp_tls = fields.get("smtp_tls_mode") or DEFAULT_SMTP_TLS_MODE
        if imap_tls not in _VALID_TLS_MODES:
            self._serve_add_account(
                error=f"Invalid IMAP TLS mode: {html.escape(imap_tls)}",
                prefill=prefill,
            )
            return
        if smtp_tls not in _VALID_TLS_MODES:
            self._serve_add_account(
                error=f"Invalid SMTP TLS mode: {html.escape(smtp_tls)}",
                prefill=prefill,
            )
            return

        # 6. Parse optional integer fields.
        try:
            imap_port = int(fields["imap_port"]) if fields.get("imap_port") else 993
        except ValueError, TypeError:
            self._serve_add_account(
                error="IMAP Port must be a number.",
                prefill=prefill,
            )
            return
        try:
            smtp_port = int(fields["smtp_port"]) if fields.get("smtp_port") else 587
        except ValueError, TypeError:
            self._serve_add_account(
                error="SMTP Port must be a number.",
                prefill=prefill,
            )
            return

        # 7. Build the MailConfig.
        from pydantic import SecretStr

        label = fields.get("label") or None
        imap_folder = fields.get("imap_folder") or "INBOX"
        db_path = f".data/{account_id}/mail.db"

        try:
            mail_cfg = MailConfig(
                imap_host=fields["imap_host"],
                smtp_host=fields["smtp_host"],
                username=fields["username"],
                password=SecretStr(fields["password"]),
                imap_port=imap_port,
                smtp_port=smtp_port,
                imap_tls_mode=imap_tls,
                smtp_tls_mode=smtp_tls,
                imap_folder=imap_folder,
                db_path=db_path,
            )
        except Exception as exc:
            self._serve_add_account(
                error=f"Invalid configuration: {html.escape(str(exc))}",
                prefill=prefill,
            )
            return

        account = MailAccount(
            account_id=account_id,
            config=mail_cfg,
            label=label,
        )

        # 8. Load existing config, append, save.
        try:
            existing = load_accounts()
        except Exception:
            # No existing config or empty — create a fresh one.
            existing = None

        if existing is not None:
            if account_id in existing.ids():
                self._serve_add_account(
                    error=f"Account ID '{html.escape(account_id)}' already exists.",
                    prefill=prefill,
                )
                return
            new_accounts = [*list(existing.accounts), account]
            default_id = existing.default_account_id
        else:
            new_accounts = [account]
            default_id = account_id

        new_config = MailAccountsConfig(
            accounts=new_accounts,
            default_account_id=default_id,
        )

        try:
            save_accounts(new_config)
        except Exception as exc:
            logger.error("Failed to save config after adding account: %s", exc)
            self._serve_add_account(
                error=f"Failed to save configuration: {html.escape(str(exc))}",
                prefill=prefill,
            )
            return

        logger.info("Added account %r via web UI", account_id)

        # Update the handler factory's cached accounts so the redirect
        # immediately reflects the new account without a server restart.
        # The handler is built via functools.partial; updating its
        # keywords dict causes the next handler instance to receive the
        # updated config.
        handler_factory = getattr(self.server, "RequestHandlerClass", None)
        if handler_factory is not None and hasattr(handler_factory, "keywords"):
            kw = handler_factory.keywords  # type: ignore[union-attr]
            if "accounts" in kw:
                kw["accounts"] = new_config

        self._redirect("/board", code=303)


def _build_add_account_form_html(
    *,
    error: str = "",
    prefill: dict[str, str] | None = None,
) -> str:
    """Build the HTML page for the add-account form."""
    p = prefill or {}

    def val(key: str, default: str = "") -> str:
        """Return the pre-filled value, HTML-escaped."""
        return html.escape(p.get(key, default), quote=True)

    error_html = (
        f'<div class="error-banner">{html.escape(error)}</div>\n' if error else ""
    )

    imap_tls = p.get("imap_tls_mode", DEFAULT_IMAP_TLS_MODE)
    smtp_tls = p.get("smtp_tls_mode", DEFAULT_SMTP_TLS_MODE)
    imap_folder = p.get("imap_folder", "INBOX")
    imap_port = p.get("imap_port", "993")
    smtp_port = p.get("smtp_port", "587")

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<title>Add Mail Account</title>\n"
        f"<style>{_ADD_ACCOUNT_FORM_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        "<h1>Add Mail Account</h1>\n"
        f"{error_html}"
        '<form method="post" action="/add-account">\n'
        # account_id
        "<label>Account ID"
        '<input name="account_id" required'
        f' value="{val("account_id")}"'
        ' pattern="[A-Za-z0-9._-]+"'
        ' placeholder="e.g. personal, work"'
        ">\n"
        "</label>\n"
        # label
        "<label>Label (optional)"
        '<input name="label" value="' + val("label") + '"'
        ' placeholder="e.g. Personal Gmail">\n'
        "</label>\n"
        # imap_host
        "<label>IMAP Host"
        '<input name="imap_host" required'
        f' value="{val("imap_host")}"'
        ' placeholder="imap.example.com">\n'
        "</label>\n"
        # smtp_host
        "<label>SMTP Host"
        '<input name="smtp_host" required'
        f' value="{val("smtp_host")}"'
        ' placeholder="smtp.example.com">\n'
        "</label>\n"
        # username
        "<label>Username"
        '<input name="username" required'
        f' value="{val("username")}"'
        ' placeholder="me@example.com">\n'
        "</label>\n"
        # password
        "<label>Password"
        '<input type="password" name="password" required'
        ' placeholder="App-specific password or account password">\n'
        "</label>\n"
        # Advanced settings — collapsed by default.
        "<details>\n"
        "<summary>Advanced settings</summary>\n"
        "<label>IMAP Port"
        '<input name="imap_port" type="number"'
        f' value="{imap_port}">\n'
        "</label>\n"
        "<label>SMTP Port"
        '<input name="smtp_port" type="number"'
        f' value="{smtp_port}">\n'
        "</label>\n"
        "<label>IMAP TLS Mode"
        '<select name="imap_tls_mode">\n'
        '<option value="direct-tls"'
        f"{' selected' if imap_tls == 'direct-tls' else ''}"
        ">direct-tls</option>\n"
        '<option value="starttls"'
        f"{' selected' if imap_tls == 'starttls' else ''}"
        ">starttls</option>\n"
        '<option value="none"'
        f"{' selected' if imap_tls == 'none' else ''}"
        ">none</option>\n"
        "</select>\n"
        "</label>\n"
        "<label>SMTP TLS Mode"
        '<select name="smtp_tls_mode">\n'
        '<option value="starttls"'
        f"{' selected' if smtp_tls == 'starttls' else ''}"
        ">starttls</option>\n"
        '<option value="direct-tls"'
        f"{' selected' if smtp_tls == 'direct-tls' else ''}"
        ">direct-tls</option>\n"
        '<option value="none"'
        f"{' selected' if smtp_tls == 'none' else ''}"
        ">none</option>\n"
        "</select>\n"
        "</label>\n"
        "<label>IMAP Folder"
        '<input name="imap_folder"'
        f' value="{html.escape(imap_folder, quote=True)}">\n'
        "</label>\n"
        "</details>\n"
        # Actions
        '<div class="form-actions">\n'
        '<button type="submit">Add Account</button>\n'
        '<a class="cancel-link" href="/board">Cancel</a>\n'
        "</div>\n"
        "</form>\n"
        "</body>\n"
        "</html>"
    )
