"""Unit tests for ``_handle_add_account()`` validation and error paths."""

from __future__ import annotations

from unittest import mock

from tests.server._test_helpers import _AccountMixinFakeHandler, _make_post_body


class TestHandleAddAccountValidation:
    """Tests for ``_handle_add_account()`` validation and error paths."""

    def _setup_post(self, handler: _AccountMixinFakeHandler, body_str: str) -> None:
        """Configure handler mocks for a POST with the given body."""
        handler.headers.get.return_value = str(len(body_str))
        handler.rfile.read.return_value = body_str.encode("utf-8")

    # -- Required fields ---------------------------------------------------

    def test_missing_account_id(self) -> None:
        handler = _AccountMixinFakeHandler()
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
        handler = _AccountMixinFakeHandler()
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
        handler = _AccountMixinFakeHandler()
        self._setup_post(handler, _make_post_body(account_id="bad id!"))
        handler._handle_add_account()
        handler._redirect.assert_not_called()
        handler._send_response.assert_called_once()
        body = handler._send_response.call_args[0][0]
        assert "invalid characters" in body

    def test_valid_account_id_passes_charset_check(self) -> None:
        """Valid ID passes charset check; test fails later (duplicate / save)."""
        handler = _AccountMixinFakeHandler()
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
        handler = _AccountMixinFakeHandler()
        self._setup_post(handler, _make_post_body(imap_tls_mode="bad-tls"))
        handler._handle_add_account()
        handler._redirect.assert_not_called()
        handler._send_response.assert_called_once()
        body = handler._send_response.call_args[0][0]
        assert "Invalid IMAP TLS mode" in body

    def test_invalid_smtp_tls_mode(self) -> None:
        handler = _AccountMixinFakeHandler()
        self._setup_post(handler, _make_post_body(smtp_tls_mode="bad-tls"))
        handler._handle_add_account()
        handler._redirect.assert_not_called()
        handler._send_response.assert_called_once()
        body = handler._send_response.call_args[0][0]
        assert "Invalid SMTP TLS mode" in body

    def test_valid_tls_modes_accepted(self) -> None:
        handler = _AccountMixinFakeHandler()
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
        handler = _AccountMixinFakeHandler()
        self._setup_post(handler, _make_post_body(imap_port="abc"))
        handler._handle_add_account()
        handler._redirect.assert_not_called()
        handler._send_response.assert_called_once()
        body = handler._send_response.call_args[0][0]
        assert "IMAP Port must be a number" in body

    def test_non_numeric_smtp_port(self) -> None:
        handler = _AccountMixinFakeHandler()
        self._setup_post(handler, _make_post_body(smtp_port="xyz"))
        handler._handle_add_account()
        handler._redirect.assert_not_called()
        handler._send_response.assert_called_once()
        body = handler._send_response.call_args[0][0]
        assert "SMTP Port must be a number" in body

    def test_valid_numeric_ports_accepted(self) -> None:
        handler = _AccountMixinFakeHandler()
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
        handler = _AccountMixinFakeHandler()
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
