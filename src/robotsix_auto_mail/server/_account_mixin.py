"""Add-account mixin for the board server — form + handler for creating
a new mail account through the web UI."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import html
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, quote, urlsplit

from robotsix_auto_mail.config import (
    MailAccount,
    MailAccountsConfig,
    MailConfig,
    load_accounts,
    save_accounts,
)
from robotsix_auto_mail.config.detect import (
    MailProvider,
    autoconfig_lookup,
    mx_lookup,
    provider_from_mx,
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
_ADD_ACCOUNT_FORM_CSS = (
    Path(__file__).parent / "static" / "add-account-standalone.css"
).read_text()

_ADD_ACCOUNT_EMBED_CSS = (
    Path(__file__).parent / "static" / "add-account-embed.css"
).read_text()


class _AccountMixin:
    """Mixin providing the add-account endpoint for the board."""

    if TYPE_CHECKING:
        self: BoardHandlerProtocol

    # -- GET /add-account --------------------------------------------------

    def _serve_add_account(
        self,
        error: str = "",
        success: str = "",
        prefill: dict[str, str] | None = None,
        origin: str = "",
    ) -> None:
        """Serve the account-creation form (GET) or re-render on error (POST).

        When *origin* is ``"settings"`` the form renders without the
        standalone page chrome (no ``<html>`` / ``<body>`` wrappers) so it
        can be embedded inside the settings page via an iframe.  A hidden
        ``origin`` field preserves the value across form submissions so
        the POST handler knows where to redirect on success.

        On GET the *origin* is read from the ``?origin=`` query param;
        on POST re-render the caller passes it explicitly and the query
        string is empty so the fallback is a no-op.
        """
        # On GET, read origin from the query string; on POST re-render,
        # the caller passes it explicitly and the query string is empty.
        path = getattr(self, "path", "")
        qs_origin = parse_qs(urlsplit(path).query).get("origin", [""])[0]
        if qs_origin:
            origin = qs_origin
        p = prefill or {}
        body = _build_add_account_form_html(
            error=error,
            success=success,
            prefill=p,
            origin=origin,
        )
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

        # 2b. Read the origin tracking field (set when embedded in
        #     the settings page).
        origin = (body.get("origin", [""])[0]).strip()

        # 2c. If the "Detect Settings" button was pressed, run provider
        #     detection and re-render the form with the detected values
        #     (or an error).
        action = (body.get("action", [""])[0]).strip()
        if action == "detect":
            email = fields.get("username", "").strip()
            if not email:
                self._serve_add_account(
                    error="Enter an email address in the Username field,"
                    " then click Detect Settings.",
                    prefill=prefill,
                    origin=origin,
                )
                return
            provider, detect_error = _detect_provider_for_email(email)
            if provider is not None:
                prefill["imap_host"] = provider.imap_host
                prefill["smtp_host"] = provider.smtp_host
                prefill["imap_port"] = str(provider.imap_port)
                prefill["smtp_port"] = str(provider.smtp_port)
                prefill["imap_tls_mode"] = provider.imap_tls_mode
                prefill["smtp_tls_mode"] = provider.smtp_tls_mode
                self._serve_add_account(
                    success=(
                        f"Settings detected for {html.escape(email)}:"
                        f" IMAP {provider.imap_host}:{provider.imap_port},"
                        f" SMTP {provider.smtp_host}:{provider.smtp_port}."
                        " Review and edit if needed, then click Add Account."
                    ),
                    prefill=prefill,
                    origin=origin,
                )
                return
            else:
                self._serve_add_account(
                    error=(
                        f"Could not auto-detect settings for"
                        f" {html.escape(email)}."
                        f" {detect_error or 'No provider match found.'}"
                        " Please enter IMAP/SMTP settings manually."
                    ),
                    prefill=prefill,
                    origin=origin,
                )
                return

        # 3. Validate required fields.
        missing = [f for f in _REQUIRED_FIELDS if not fields.get(f)]
        if missing:
            self._serve_add_account(
                error=f"Missing required fields: {', '.join(missing)}",
                prefill=prefill,
                origin=origin,
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
                origin=origin,
            )
            return

        # 5. Validate TLS mode values.
        imap_tls = fields.get("imap_tls_mode") or DEFAULT_IMAP_TLS_MODE
        smtp_tls = fields.get("smtp_tls_mode") or DEFAULT_SMTP_TLS_MODE
        if imap_tls not in _VALID_TLS_MODES:
            self._serve_add_account(
                error=f"Invalid IMAP TLS mode: {html.escape(imap_tls)}",
                prefill=prefill,
                origin=origin,
            )
            return
        if smtp_tls not in _VALID_TLS_MODES:
            self._serve_add_account(
                error=f"Invalid SMTP TLS mode: {html.escape(smtp_tls)}",
                prefill=prefill,
                origin=origin,
            )
            return

        # 6. Parse optional integer fields.
        try:
            imap_port = int(fields["imap_port"]) if fields.get("imap_port") else 993
        except (ValueError, TypeError):  # fmt: skip
            self._serve_add_account(
                error="IMAP Port must be a number.",
                prefill=prefill,
                origin=origin,
            )
            return
        try:
            smtp_port = int(fields["smtp_port"]) if fields.get("smtp_port") else 587
        except (ValueError, TypeError):  # fmt: skip
            self._serve_add_account(
                error="SMTP Port must be a number.",
                prefill=prefill,
                origin=origin,
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
                origin=origin,
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
                    origin=origin,
                )
                return
            new_accounts = [*list(existing.accounts), account]
        else:
            new_accounts = [account]

        try:
            new_config = (
                existing.with_accounts(new_accounts)
                if existing is not None
                else MailAccountsConfig(
                    accounts=new_accounts,
                )
            )
        except Exception as exc:
            self._serve_add_account(
                error=f"Invalid configuration: {html.escape(str(exc))}",
                prefill=prefill,
                origin=origin,
            )
            return

        try:
            save_accounts(new_config)
        except Exception as exc:
            logger.error("Failed to save config after adding account: %s", exc)
            self._serve_add_account(
                error=f"Failed to save configuration: {html.escape(str(exc))}",
                prefill=prefill,
                origin=origin,
            )
            return

        # Initialize the new account's database and seed its settings
        # store so the account config is persisted in the managed
        # configuration plane (the per-account SQLite DB).  This ensures
        # the account survives even when the deploy system overwrites
        # config/config.json on restart — the settings store is the
        # authoritative source for runtime config.
        try:
            from robotsix_auto_mail.db import init_db
            from robotsix_auto_mail.settings import SettingsStore

            conn = init_db(db_path, skip_migrations=True)
            try:
                store = SettingsStore(db_path)
                store.seed_from_mail_config(conn, mail_cfg)
            finally:
                conn.close()
        except Exception:
            logger.exception("Failed to seed settings store for account %r", account_id)
            # The account was saved to config; the settings-store seeding
            # is a best-effort addition — do not block the redirect.

        logger.info("Added account %r via web UI", account_id)

        # Update the handler factory's cached accounts so the redirect
        # immediately reflects the new account without a server restart.
        # The handler is built via functools.partial; updating its
        # keywords dict causes the next handler instance to receive the
        # updated config.
        handler_factory = getattr(self.server, "RequestHandlerClass", None)
        if handler_factory is not None and hasattr(handler_factory, "keywords"):
            kw = handler_factory.keywords
            if "accounts" in kw:
                kw["accounts"] = new_config

        if origin == "settings":
            # Redirect the parent (settings page) rather than the iframe.
            target = "/settings-panel?added=" + quote(account_id, safe="")
            self._send_response(
                "<!DOCTYPE html>\n"
                "<script>window.top.location.href="
                + json.dumps(target)
                + ";</script>\n",
                status=200,
                content_type="text/html; charset=utf-8",
            )
        else:
            self._redirect("/board", code=303)


def _build_add_account_form_html(
    *,
    error: str = "",
    success: str = "",
    prefill: dict[str, str] | None = None,
    origin: str = "",
) -> str:
    """Build the HTML for the add-account form.

    When *origin* is ``"settings"`` the output is a bare form fragment
    (no ``<html>`` / ``<body>`` wrappers) suitable for embedding inside
    the settings page via an iframe.
    """
    p = prefill or {}

    def val(key: str, default: str = "") -> str:
        """Return the pre-filled value, HTML-escaped."""
        return html.escape(p.get(key, default), quote=True)

    banner_html = ""
    if error:
        banner_html = f'<div class="error-banner">{html.escape(error)}</div>\n'
    elif success:
        banner_html = f'<div class="success-banner">{html.escape(success)}</div>\n'

    imap_tls = p.get("imap_tls_mode", DEFAULT_IMAP_TLS_MODE)
    smtp_tls = p.get("smtp_tls_mode", DEFAULT_SMTP_TLS_MODE)
    imap_folder = p.get("imap_folder", "INBOX")
    imap_port = p.get("imap_port", "993")
    smtp_port = p.get("smtp_port", "587")

    origin_val = html.escape(origin, quote=True)
    origin_input = (
        f'<input type="hidden" name="origin" value="{origin_val}">\n' if origin else ""
    )

    form_inner = (
        '<form method="post" action="/add-account">\n'
        + origin_input
        # account_id
        + "<label>Account ID"
        '<input name="account_id" required'
        f' value="{val("account_id")}"'
        ' pattern="[A-Za-z0-9._-]+"'
        ' placeholder="e.g. personal, work"'
        ">\n"
        "</label>\n"
        # label
         + "<label>Label (optional)"
        '<input name="label" value="' + val("label") + '"'
        ' placeholder="e.g. Personal Gmail">\n'
        "</label>\n"
        # imap_host
         + "<label>IMAP Host"
        '<input name="imap_host" required'
        f' value="{val("imap_host")}"'
        ' placeholder="imap.example.com">\n'
        "</label>\n"
        # smtp_host
         + "<label>SMTP Host"
        '<input name="smtp_host" required'
        f' value="{val("smtp_host")}"'
        ' placeholder="smtp.example.com">\n'
        "</label>\n"
        # username
         + "<label>Username"
        '<input name="username" required'
        f' value="{val("username")}"'
        ' placeholder="me@example.com">\n'
        "</label>\n"
        # password
         + "<label>Password"
        '<input type="password" name="password" required'
        ' placeholder="App-specific password or account password">\n'
        "</label>\n"
        # Advanced settings — collapsed by default.
         + "<details>\n"
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
        '<button type="submit" name="action" value="add">Add Account</button>\n'
        '<button type="submit" name="action" value="detect"'
        " formnovalidate>Detect Settings</button>\n"
        + (
            '<a class="cancel-link" href="/settings-panel">Cancel</a>\n'
            if origin == "settings"
            else '<a class="cancel-link" href="/board">Cancel</a>\n'
        )
        + "</div>\n"
        "</form>\n"
    )

    # Embed mode: bare form fragment with minimal inline styles.
    if origin == "settings":
        return (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '<meta charset="utf-8">\n'
            f"<style>{_ADD_ACCOUNT_EMBED_CSS}</style>\n"
            "</head>\n"
            "<body>\n"
            f"{banner_html}"
            f"{form_inner}"
            "</body>\n"
            "</html>"
        )

    # Standalone page: full HTML document.
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
        f"{banner_html}"
        f"{form_inner}"
        "</body>\n"
        "</html>"
    )


def _detect_provider_for_email(
    email_address: str,
) -> tuple[MailProvider | None, str]:
    """Run the provider-detection ladder for *email_address*.

    Tries the non-LLM detection paths only (autoconfig + MX lookup)
    so the web UI stays responsive even without an LLM API key.

    Returns ``(provider, error_message)`` — exactly one of the two
    is non-truthy.
    """
    # 1. Autoconfig lookup (Mozilla ISPDB + domain autoconfig endpoint).
    provider = autoconfig_lookup(email_address, timeout=4.0)
    if provider is not None:
        return provider, ""

    # 2. MX-record lookup → pattern-match against known providers.
    mx_hosts = mx_lookup(email_address, timeout=4.0)
    provider = provider_from_mx(mx_hosts)
    if provider is not None:
        return provider, ""

    return None, "The email domain is not in the known-provider database."
