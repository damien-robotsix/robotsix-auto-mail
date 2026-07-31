"""Unit tests for ``_handle_add_account()`` config construction, duplicate
detection, and save paths."""

from __future__ import annotations

from typing import Any
from unittest import mock

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from tests.server._test_helpers import _AccountMixinFakeHandler, _make_post_body


class TestHandleAddAccountConfigSave:
    """Tests for ``_handle_add_account()`` config construction, duplicate
    detection, and save paths."""

    def _setup_post(self, handler: _AccountMixinFakeHandler, body_str: str) -> None:
        handler.headers.get.return_value = str(len(body_str))
        handler.rfile.read.return_value = body_str.encode("utf-8")

    def test_mailconfig_construction_failure(self) -> None:
        """When MailConfig() raises, the error is caught and rendered."""
        handler = _AccountMixinFakeHandler()
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
        handler = _AccountMixinFakeHandler()
        self._setup_post(handler, _make_post_body(account_id="existing"))

        existing_account = MailAccount(
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
        handler = _AccountMixinFakeHandler()
        self._setup_post(handler, _make_post_body())
        with (
            mock.patch(
                "robotsix_auto_mail.server._account_mixin.load_accounts",
                side_effect=FileNotFoundError,
            ),
            mock.patch(
                "robotsix_auto_mail.server._account_mixin.save_accounts",
                side_effect=OSError("disk full"),
            ),
        ):
            handler._handle_add_account()
        handler._redirect.assert_not_called()
        handler._send_response.assert_called_once()
        body = handler._send_response.call_args[0][0]
        assert "Failed to save configuration" in body
        assert "disk full" in body

    def test_success_redirects_to_board(self) -> None:
        handler = _AccountMixinFakeHandler()
        # Give the handler a server with RequestHandlerClass.keywords
        handler.server.RequestHandlerClass.keywords = {"accounts": None}
        self._setup_post(handler, _make_post_body())

        with (
            mock.patch(
                "robotsix_auto_mail.server._account_mixin.load_accounts",
                side_effect=FileNotFoundError,
            ),
            mock.patch(
                "robotsix_auto_mail.server._account_mixin.save_accounts",
            ),
        ):
            handler._handle_add_account()

        handler._redirect.assert_called_once_with("/board", code=303)

    def test_success_updates_handler_factory_cache(self) -> None:
        handler = _AccountMixinFakeHandler()
        keywords: dict[str, Any] = {"accounts": None}
        handler.server.RequestHandlerClass.keywords = keywords
        self._setup_post(handler, _make_post_body())

        with (
            mock.patch(
                "robotsix_auto_mail.server._account_mixin.load_accounts",
                side_effect=FileNotFoundError,
            ),
            mock.patch(
                "robotsix_auto_mail.server._account_mixin.save_accounts",
            ),
        ):
            handler._handle_add_account()

        # The keywords['accounts'] should have been updated to the new config
        assert keywords["accounts"] is not None
        assert isinstance(keywords["accounts"], MailAccountsConfig)
        assert keywords["accounts"].default_account_id == "test"

    def test_success_appends_to_existing_config(self) -> None:
        handler = _AccountMixinFakeHandler()
        handler.server.RequestHandlerClass.keywords = {"accounts": None}
        self._setup_post(handler, _make_post_body(account_id="new-account"))

        existing_account = MailAccount(
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

        with (
            mock.patch(
                "robotsix_auto_mail.server._account_mixin.load_accounts",
                return_value=existing_config,
            ),
            mock.patch(
                "robotsix_auto_mail.server._account_mixin.save_accounts",
            ) as mock_save,
        ):
            handler._handle_add_account()

        mock_save.assert_called_once()
        saved_config = mock_save.call_args[0][0]
        assert len(saved_config.accounts) == 2
        assert saved_config.accounts[0].account_id == "old-account"
        assert saved_config.accounts[1].account_id == "new-account"
        # default_account_id preserved from existing
        assert saved_config.default_account_id == "old-account"

    def test_empty_accounts_with_blank_default_id_adds_first_account(self) -> None:
        """Regression: when the existing config has accounts=[] and
        default_account_id='' (fresh-deploy seed), adding the first
        account must succeed and set the new account as default."""
        handler = _AccountMixinFakeHandler()
        handler.server.RequestHandlerClass.keywords = {"accounts": None}
        self._setup_post(handler, _make_post_body(account_id="first-account"))

        # Simulate a fresh-deploy config with zero accounts and blank default.
        existing_config = MailAccountsConfig(
            accounts=[],
            default_account_id="",
        )

        with (
            mock.patch(
                "robotsix_auto_mail.server._account_mixin.load_accounts",
                return_value=existing_config,
            ),
            mock.patch(
                "robotsix_auto_mail.server._account_mixin.save_accounts",
            ) as mock_save,
        ):
            handler._handle_add_account()

        # Must succeed (no 502 crash) — the new account becomes the default.
        mock_save.assert_called_once()
        saved_config = mock_save.call_args[0][0]
        assert len(saved_config.accounts) == 1
        assert saved_config.accounts[0].account_id == "first-account"
        assert saved_config.default_account_id == "first-account"

    def test_load_accounts_failure_creates_fresh_config(self) -> None:
        """When load_accounts raises (no existing config), a fresh one is created."""
        handler = _AccountMixinFakeHandler()
        handler.server.RequestHandlerClass.keywords = {"accounts": None}
        self._setup_post(handler, _make_post_body(account_id="sole-account"))

        with (
            mock.patch(
                "robotsix_auto_mail.server._account_mixin.load_accounts",
                side_effect=FileNotFoundError,
            ),
            mock.patch(
                "robotsix_auto_mail.server._account_mixin.save_accounts",
            ) as mock_save,
        ):
            handler._handle_add_account()

        mock_save.assert_called_once()
        saved_config = mock_save.call_args[0][0]
        assert len(saved_config.accounts) == 1
        assert saved_config.accounts[0].account_id == "sole-account"
        assert saved_config.default_account_id == "sole-account"
