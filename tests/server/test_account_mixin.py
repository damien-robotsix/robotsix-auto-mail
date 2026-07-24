"""Unit tests for ``_AccountMixin`` methods and ``_build_add_account_form_html``.

Drives the mixin directly against a mock handler *self*, isolating the
logic from the HTTP transport and config I/O.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailAccountsConfig, MailConfig
from robotsix_auto_mail.server._account_mixin import (
    _AccountMixin,
    _build_add_account_form_html,
)

# ---------------------------------------------------------------------------
# Fake handler factory
# ---------------------------------------------------------------------------


class _FakeHandler(_AccountMixin):
    """Concrete handler that wires ``BoardHandlerProtocol`` attributes
    to MagicMock defaults so mixin methods can be called directly."""

    def __init__(
        self,
        db_path: str = "/tmp/test.db",
        mail_config: MailConfig | None = None,
        *,
        accounts: Any = None,
    ) -> None:
        self.db_path = db_path
        self.mail_config = mail_config
        self.accounts = accounts
        self._current_account_id = None
        self._aggregate = False
        self._account_cookie = None
        self.default_account_id = None
        self.headers = mock.MagicMock()
        self.rfile = mock.MagicMock()
        self._send_response = mock.MagicMock()
        self._redirect = mock.MagicMock()
        self._not_found = mock.MagicMock()
        self._bad_request = mock.MagicMock()
        self._serve_json = mock.MagicMock()
        self.server = mock.MagicMock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FORM_BODY = "account_id=test&imap_host=h&smtp_host=h&username=u&password=p"


def _make_post_body(**overrides: str) -> str:
    """Build a URL-encoded POST body from required defaults + overrides."""
    defaults = {
        "account_id": "test",
        "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com",
        "username": "user@example.com",
        "password": "secret",
    }
    defaults.update(overrides)
    return "&".join(f"{k}={v}" for k, v in defaults.items())


# ---------------------------------------------------------------------------
# _serve_add_account
# ---------------------------------------------------------------------------


class TestServeAddAccount:
    """Tests for ``_serve_add_account()`` — GET form rendering and POST
    re-render with error / prefill."""

    def test_renders_form_without_error(self) -> None:
        handler = _FakeHandler()
        handler._serve_add_account()
        handler._send_response.assert_called_once()
        body, kwargs = handler._send_response.call_args
        assert "Add Mail Account" in body[0]
        # The CSS contains ".error-banner" as a selector; check for the
        # actual error-banner div, not just the CSS class name.
        assert '<div class="error-banner">' not in body[0]
        assert kwargs.get("content_type") == "text/html; charset=utf-8"

    def test_renders_form_with_error_banner(self) -> None:
        handler = _FakeHandler()
        handler._serve_add_account(error="Something went wrong")
        handler._send_response.assert_called_once()
        body, _kwargs = handler._send_response.call_args
        assert "error-banner" in body[0]
        assert "Something went wrong" in body[0]

    def test_renders_form_with_prefilled_values(self) -> None:
        handler = _FakeHandler()
        handler._serve_add_account(
            prefill={"account_id": "my-account", "username": "me@host.com"},
        )
        handler._send_response.assert_called_once()
        body, _kwargs = handler._send_response.call_args
        assert 'value="my-account"' in body[0]
        assert 'value="me@host.com"' in body[0]

    def test_renders_form_with_error_and_prefill(self) -> None:
        handler = _FakeHandler()
        handler._serve_add_account(
            error="Bad input",
            prefill={"imap_host": "bad-host"},
        )
        handler._send_response.assert_called_once()
        body, _kwargs = handler._send_response.call_args
        assert "error-banner" in body[0]
        assert "Bad input" in body[0]
        assert 'value="bad-host"' in body[0]


# ---------------------------------------------------------------------------
# _handle_add_account — validation paths
# ---------------------------------------------------------------------------


class TestHandleAddAccountValidation:
    """Tests for ``_handle_add_account()`` validation and error paths."""

    def _setup_post(self, handler: _FakeHandler, body_str: str) -> None:
        """Configure handler mocks for a POST with the given body."""
        handler.headers.get.return_value = str(len(body_str))
        handler.rfile.read.return_value = body_str.encode("utf-8")

    # -- Required fields ---------------------------------------------------

    def test_missing_account_id(self) -> None:
        handler = _FakeHandler()
        self._setup_post(handler, "imap_host=h&smtp_host=h&username=u&password=p")
        handler._handle_add_account()
        # Should call _serve_add_account with error, not _redirect
        handler._redirect.assert_not_called()
        # _serve_add_account forwards to _send_response
        handler._send_response.assert_called_once()
        body = handler._send_response.call_args[0][0]
        assert "Missing required fields" in body
        assert "account_id" in body

    def test_missing_multiple_fields(self) -> None:
        handler = _FakeHandler()
        self._setup_post(handler, "account_id=test")
        handler._handle_add_account()
        handler._redirect.assert_not_called()
        handler._send_response.assert_called_once()
        body = handler._send_response.call_args[0][0]
        assert "Missing required fields" in body
        # All four missing should be listed
        for field in ("imap_host", "smtp_host", "username", "password"):
            assert field in body

    # -- Account ID charset ------------------------------------------------

    def test_invalid_account_id_chars(self) -> None:
        handler = _FakeHandler()
        self._setup_post(handler, _make_post_body(account_id="bad id!"))
        handler._handle_add_account()
        handler._redirect.assert_not_called()
        handler._send_response.assert_called_once()
        body = handler._send_response.call_args[0][0]
        assert "invalid characters" in body

    def test_valid_account_id_passes_charset_check(self) -> None:
        """Valid ID passes charset check; test fails later (duplicate / save)."""
        handler = _FakeHandler()
        self._setup_post(handler, _make_post_body(account_id="valid-id.1_test"))
        with mock.patch(
            "robotsix_auto_mail.server._account_mixin.load_accounts",
            side_effect=FileNotFoundError,
        ), mock.patch(
            "robotsix_auto_mail.server._account_mixin.save_accounts",
        ):
            handler._handle_add_account()
        # Should not fail on charset; will hit save path
        # save_accounts is called (or load_accounts raises)
        # But since load_accounts raises FileNotFoundError, it falls through
        # We just want to verify charset check passed (no error message about invalid chars)
        assert not any(
            "invalid characters" in str(call)
            for call in handler._send_response.call_args_list
        )

    # -- TLS mode validation -----------------------------------------------

    def test_invalid_imap_tls_mode(self) -> None:
        handler = _FakeHandler()
        self._setup_post(handler, _make_post_body(imap_tls_mode="bad-tls"))
        handler._handle_add_account()
        handler._redirect.assert_not_called()
        handler._send_response.assert_called_once()
        body = handler._send_response.call_args[0][0]
        assert "Invalid IMAP TLS mode" in body

    def test_invalid_smtp_tls_mode(self) -> None:
        handler = _FakeHandler()
        self._setup_post(handler, _make_post_body(smtp_tls_mode="bad-tls"))
        handler._handle_add_account()
        handler._redirect.assert_not_called()
        handler._send_response.assert_called_once()
        body = handler._send_response.call_args[0][0]
        assert "Invalid SMTP TLS mode" in body

    def test_valid_tls_modes_accepted(self) -> None:
        handler = _FakeHandler()
        self._setup_post(
            handler,
            _make_post_body(imap_tls_mode="starttls", smtp_tls_mode="none"),
        )
        with mock.patch(
            "robotsix_auto_mail.server._account_mixin.load_accounts",
            side_effect=FileNotFoundError,
        ), mock.patch(
            "robotsix_auto_mail.server._account_mixin.save_accounts",
        ):
            handler._handle_add_account()
        # Should not produce TLS error
        assert not any(
            "TLS mode" in str(call)
            for call in handler._send_response.call_args_list
        )

    # -- Port parsing ------------------------------------------------------

    def test_non_numeric_imap_port(self) -> None:
        handler = _FakeHandler()
        self._setup_post(handler, _make_post_body(imap_port="abc"))
        handler._handle_add_account()
        handler._redirect.assert_not_called()
        handler._send_response.assert_called_once()
        body = handler._send_response.call_args[0][0]
        assert "IMAP Port must be a number" in body

    def test_non_numeric_smtp_port(self) -> None:
        handler = _FakeHandler()
        self._setup_post(handler, _make_post_body(smtp_port="xyz"))
        handler._handle_add_account()
        handler._redirect.assert_not_called()
        handler._send_response.assert_called_once()
        body = handler._send_response.call_args[0][0]
        assert "SMTP Port must be a number" in body

    def test_valid_numeric_ports_accepted(self) -> None:
        handler = _FakeHandler()
        self._setup_post(
            handler, _make_post_body(imap_port="993", smtp_port="587"),
        )
        with mock.patch(
            "robotsix_auto_mail.server._account_mixin.load_accounts",
            side_effect=FileNotFoundError,
        ), mock.patch(
            "robotsix_auto_mail.server._account_mixin.save_accounts",
        ):
            handler._handle_add_account()
        assert not any(
            "Port must be a number" in str(call)
            for call in handler._send_response.call_args_list
        )

    def test_empty_ports_use_defaults(self) -> None:
        """Empty port strings should use defaults (993/587)."""
        handler = _FakeHandler()
        self._setup_post(
            handler, _make_post_body(imap_port="", smtp_port=""),
        )
        with mock.patch(
            "robotsix_auto_mail.server._account_mixin.load_accounts",
            side_effect=FileNotFoundError,
        ), mock.patch(
            "robotsix_auto_mail.server._account_mixin.save_accounts",
        ):
            handler._handle_add_account()
        # Should not produce port error
        assert not any(
            "Port must be a number" in str(call)
            for call in handler._send_response.call_args_list
        )


# ---------------------------------------------------------------------------
# _handle_add_account — config / save paths
# ---------------------------------------------------------------------------


class TestHandleAddAccountConfigSave:
    """Tests for ``_handle_add_account()`` config construction, duplicate
    detection, and save paths."""

    def _setup_post(self, handler: _FakeHandler, body_str: str) -> None:
        handler.headers.get.return_value = str(len(body_str))
        handler.rfile.read.return_value = body_str.encode("utf-8")

    def test_mailconfig_construction_failure(self) -> None:
        """When MailConfig() raises, the error is caught and rendered."""
        handler = _FakeHandler()
        self._setup_post(handler, _make_post_body())
        with mock.patch(
            "robotsix_auto_mail.server._account_mixin.MailConfig",
            side_effect=ValueError("bad field"),
        ):
            handler._handle_add_account()
        handler._redirect.assert_not_called()
        handler._send_response.assert_called_once()
        body = handler._send_response.call_args[0][0]
        assert "Invalid configuration" in body
        assert "bad field" in body

    def test_duplicate_account_id(self) -> None:
        handler = _FakeHandler()
        self._setup_post(handler, _make_post_body(account_id="existing"))

        from robotsix_auto_mail.config import MailAccount as RealMailAccount

        existing_account = RealMailAccount(
            account_id="existing",
            config=MailConfig(
                imap_host="h",
                smtp_host="h",
                username="u",
                password="p",  # type: ignore[arg-type]
                imap_port=993,
                smtp_port=587,
            ),
        )
        existing_config = MailAccountsConfig(
            accounts=[existing_account],
            default_account_id="existing",
        )

        with mock.patch(
            "robotsix_auto_mail.server._account_mixin.load_accounts",
            return_value=existing_config,
        ):
            handler._handle_add_account()

        handler._redirect.assert_not_called()
        handler._send_response.assert_called_once()
        body = handler._send_response.call_args[0][0]
        assert "already exists" in body

    def test_config_save_failure(self) -> None:
        handler = _FakeHandler()
        self._setup_post(handler, _make_post_body())
        with mock.patch(
            "robotsix_auto_mail.server._account_mixin.load_accounts",
            side_effect=FileNotFoundError,
        ), mock.patch(
            "robotsix_auto_mail.server._account_mixin.save_accounts",
            side_effect=OSError("disk full"),
        ):
            handler._handle_add_account()
        handler._redirect.assert_not_called()
        handler._send_response.assert_called_once()
        body = handler._send_response.call_args[0][0]
        assert "Failed to save configuration" in body
        assert "disk full" in body

    def test_success_redirects_to_board(self) -> None:
        handler = _FakeHandler()
        # Give the handler a server with RequestHandlerClass.keywords
        handler.server.RequestHandlerClass.keywords = {"accounts": None}
        self._setup_post(handler, _make_post_body())

        with mock.patch(
            "robotsix_auto_mail.server._account_mixin.load_accounts",
            side_effect=FileNotFoundError,
        ), mock.patch(
            "robotsix_auto_mail.server._account_mixin.save_accounts",
        ):
            handler._handle_add_account()

        handler._redirect.assert_called_once_with("/board", code=303)

    def test_success_updates_handler_factory_cache(self) -> None:
        handler = _FakeHandler()
        keywords: dict[str, Any] = {"accounts": None}
        handler.server.RequestHandlerClass.keywords = keywords
        self._setup_post(handler, _make_post_body())

        with mock.patch(
            "robotsix_auto_mail.server._account_mixin.load_accounts",
            side_effect=FileNotFoundError,
        ), mock.patch(
            "robotsix_auto_mail.server._account_mixin.save_accounts",
        ):
            handler._handle_add_account()

        # The keywords['accounts'] should have been updated to the new config
        assert keywords["accounts"] is not None
        assert isinstance(keywords["accounts"], MailAccountsConfig)
        assert keywords["accounts"].default_account_id == "test"

    def test_success_appends_to_existing_config(self) -> None:
        handler = _FakeHandler()
        handler.server.RequestHandlerClass.keywords = {"accounts": None}
        self._setup_post(handler, _make_post_body(account_id="new-account"))

        from robotsix_auto_mail.config import MailAccount as RealMailAccount

        existing_account = RealMailAccount(
            account_id="old-account",
            config=MailConfig(
                imap_host="h",
                smtp_host="h",
                username="u",
                password="p",  # type: ignore[arg-type]
                imap_port=993,
                smtp_port=587,
            ),
        )
        existing_config = MailAccountsConfig(
            accounts=[existing_account],
            default_account_id="old-account",
        )

        with mock.patch(
            "robotsix_auto_mail.server._account_mixin.load_accounts",
            return_value=existing_config,
        ), mock.patch(
            "robotsix_auto_mail.server._account_mixin.save_accounts",
        ) as mock_save:
            handler._handle_add_account()

        mock_save.assert_called_once()
        saved_config = mock_save.call_args[0][0]
        assert len(saved_config.accounts) == 2
        assert saved_config.accounts[0].account_id == "old-account"
        assert saved_config.accounts[1].account_id == "new-account"
        # default_account_id preserved from existing
        assert saved_config.default_account_id == "old-account"

    def test_load_accounts_failure_creates_fresh_config(self) -> None:
        """When load_accounts raises (no existing config), a fresh one is created."""
        handler = _FakeHandler()
        handler.server.RequestHandlerClass.keywords = {"accounts": None}
        self._setup_post(handler, _make_post_body(account_id="sole-account"))

        with mock.patch(
            "robotsix_auto_mail.server._account_mixin.load_accounts",
            side_effect=FileNotFoundError,
        ), mock.patch(
            "robotsix_auto_mail.server._account_mixin.save_accounts",
        ) as mock_save:
            handler._handle_add_account()

        mock_save.assert_called_once()
        saved_config = mock_save.call_args[0][0]
        assert len(saved_config.accounts) == 1
        assert saved_config.accounts[0].account_id == "sole-account"
        assert saved_config.default_account_id == "sole-account"


# ---------------------------------------------------------------------------
# _build_add_account_form_html
# ---------------------------------------------------------------------------


class TestBuildAddAccountFormHtml:
    """Tests for the standalone ``_build_add_account_form_html()`` function."""

    def test_contains_required_form_fields(self) -> None:
        html_out = _build_add_account_form_html()
        assert 'name="account_id"' in html_out
        assert 'name="imap_host"' in html_out
        assert 'name="smtp_host"' in html_out
        assert 'name="username"' in html_out
        assert 'name="password"' in html_out

    def test_contains_advanced_settings(self) -> None:
        html_out = _build_add_account_form_html()
        assert 'name="imap_port"' in html_out
        assert 'name="smtp_port"' in html_out
        assert 'name="imap_tls_mode"' in html_out
        assert 'name="smtp_tls_mode"' in html_out
        assert 'name="imap_folder"' in html_out

    def test_no_error_banner_when_error_empty(self) -> None:
        html_out = _build_add_account_form_html(error="")
        # The CSS contains ".error-banner" as a selector; check the div.
        assert '<div class="error-banner">' not in html_out

    def test_no_error_banner_when_error_default(self) -> None:
        html_out = _build_add_account_form_html()
        assert '<div class="error-banner">' not in html_out

    def test_error_banner_when_error_non_empty(self) -> None:
        html_out = _build_add_account_form_html(error="Oops!")
        assert "error-banner" in html_out
        assert "Oops!" in html_out

    def test_prefilled_values_appear(self) -> None:
        html_out = _build_add_account_form_html(
            prefill={"account_id": "my-id", "username": "me@host.com"},
        )
        assert 'value="my-id"' in html_out
        assert 'value="me@host.com"' in html_out

    def test_html_escapes_prefilled_values(self) -> None:
        html_out = _build_add_account_form_html(
            prefill={"account_id": '<script>alert("xss")</script>'},
        )
        assert '<script>alert("xss")</script>' not in html_out
        # The escaped version should contain &lt; and &gt;
        assert "&lt;script&gt;" in html_out

    def test_html_escapes_error_message(self) -> None:
        html_out = _build_add_account_form_html(error='<b>bold</b>')
        assert "<b>bold</b>" not in html_out
        assert "&lt;b&gt;bold&lt;/b&gt;" in html_out

    def test_prefilled_tls_mode_selected(self) -> None:
        html_out = _build_add_account_form_html(
            prefill={"imap_tls_mode": "starttls", "smtp_tls_mode": "none"},
        )
        assert '<option value="starttls" selected>starttls</option>' in html_out
        assert '<option value="none" selected>none</option>' in html_out

    def test_default_tls_mode_selected_when_no_prefill(self) -> None:
        html_out = _build_add_account_form_html()
        # IMAP default = direct-tls
        assert '<option value="direct-tls" selected>direct-tls</option>' in html_out
        # SMTP default = starttls
        assert '<option value="starttls" selected>starttls</option>' in html_out

    def test_prefilled_imap_folder(self) -> None:
        html_out = _build_add_account_form_html(
            prefill={"imap_folder": "[Gmail]/All Mail"},
        )
        assert 'value="[Gmail]/All Mail"' in html_out

    def test_form_action_and_method(self) -> None:
        html_out = _build_add_account_form_html()
        assert 'method="post"' in html_out
        assert 'action="/add-account"' in html_out

    def test_cancel_link(self) -> None:
        html_out = _build_add_account_form_html()
        assert 'href="/board"' in html_out
        assert "Cancel" in html_out

    def test_submit_button(self) -> None:
        html_out = _build_add_account_form_html()
        assert "Add Account" in html_out
        assert 'type="submit"' in html_out

    def test_password_not_prefilled(self) -> None:
        """Password field should never be pre-filled for security."""
        html_out = _build_add_account_form_html(
            prefill={"password": "secret123"},
        )
        # Password field should NOT contain the value
        assert 'value="secret123"' not in html_out
