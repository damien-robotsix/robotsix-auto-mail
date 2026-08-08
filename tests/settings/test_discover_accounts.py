"""Positive-path tests for ``discover_accounts_from_settings_stores``
and the ``merge_settings_store_accounts`` → ``_cmd_serve`` integration.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.db import init_db
from robotsix_auto_mail.settings import SettingsStore
from robotsix_auto_mail.settings.store import (
    discover_accounts_from_settings_stores,
    merge_settings_store_accounts,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _populated_store_db(db_path: str, cfg: MailConfig) -> None:
    """Create a file-backed DB at *db_path*, seed it with *cfg*, and close."""
    conn = init_db(db_path)
    try:
        store = SettingsStore(db_path)
        store.seed_from_mail_config(conn, cfg)
    finally:
        conn.close()


def _empty_store_db(db_path: str) -> None:
    """Create a file-backed DB at *db_path* with no settings rows."""
    conn = init_db(db_path)
    conn.close()


# ---------------------------------------------------------------------------
# discover_accounts_from_settings_stores
# ---------------------------------------------------------------------------


class TestDiscoverAccountsFromSettingsStores:
    """Positive-path tests for the discovery function."""

    def test_discovers_account_from_populated_store(
        self,
        tmp_path: Path,
        cfg: MailConfig,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A store with a full MailConfig yields one discovered account with
        correct account_id and db_path."""
        data_dir = tmp_path / ".data"
        account_id = "abc123"
        db_file = data_dir / account_id / "mail.db"
        db_file.parent.mkdir(parents=True)

        _populated_store_db(str(db_file), cfg)

        caplog.set_level(logging.INFO)
        discovered = discover_accounts_from_settings_stores(str(data_dir))

        assert len(discovered) == 1
        account = discovered[0]
        assert account.account_id == account_id
        assert account.config.db_path == str(db_file)
        # Non-db_path fields should match the seed config.
        assert account.config.imap_host == cfg.imap_host
        assert account.config.username == cfg.username
        assert (
            account.config.password.get_secret_value()
            == cfg.password.get_secret_value()
        )

        assert "Discovered account" in caplog.text
        assert account_id in caplog.text

    def test_empty_store_is_skipped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A DB file that exists but has no component_settings rows is skipped."""
        data_dir = tmp_path / ".data"
        account_id = "empty-account"
        db_file = data_dir / account_id / "mail.db"
        db_file.parent.mkdir(parents=True)

        _empty_store_db(str(db_file))

        discovered = discover_accounts_from_settings_stores(str(data_dir))
        assert discovered == []

    def test_corrupt_db_is_skipped_with_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A file that is not a valid SQLite database is skipped with a
        warning log and no exception propagates."""
        data_dir = tmp_path / ".data"
        account_id = "corrupt"
        db_file = data_dir / account_id / "mail.db"
        db_file.parent.mkdir(parents=True)
        db_file.write_text("this is not a sqlite database")

        caplog.set_level(logging.WARNING)
        discovered = discover_accounts_from_settings_stores(str(data_dir))

        assert discovered == []
        # The corrupt-db path should emit a warning for the account.
        assert "Failed to load settings store" in caplog.text
        assert account_id in caplog.text

    def test_missing_data_dir_returns_empty(self, tmp_path: Path) -> None:
        """A non-existent data_dir quietly returns an empty list."""
        non_existent = str(tmp_path / "does-not-exist")
        discovered = discover_accounts_from_settings_stores(non_existent)
        assert discovered == []

    def test_skips_subdir_without_db(
        self, tmp_path: Path, cfg: MailConfig, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Only subdirectories that contain a mail.db are considered; others
        are silently skipped."""
        data_dir = tmp_path / ".data"
        # Create one valid account dir + one dir without a mail.db.
        valid_id = "valid"
        valid_db = data_dir / valid_id / "mail.db"
        valid_db.parent.mkdir(parents=True)
        _populated_store_db(str(valid_db), cfg)

        no_db_dir = data_dir / "no-db"
        no_db_dir.mkdir(parents=True)
        (no_db_dir / "some_other_file.txt").write_text("hello")

        caplog.set_level(logging.INFO)
        discovered = discover_accounts_from_settings_stores(str(data_dir))

        assert len(discovered) == 1
        assert discovered[0].account_id == valid_id

    def test_skips_files_at_top_level(self, tmp_path: Path, cfg: MailConfig) -> None:
        """Files directly inside data_dir (not in subdirectories) are ignored."""
        data_dir = tmp_path / ".data"
        data_dir.mkdir(parents=True)
        # A stray file at the top level — not a subdirectory.
        (data_dir / "not-a-dir.txt").write_text("ignore me")

        discovered = discover_accounts_from_settings_stores(str(data_dir))
        assert discovered == []

    def test_foreign_schema_db_is_skipped_with_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A valid SQLite DB whose schema lacks the component_settings table
        is skipped with a warning."""
        data_dir = tmp_path / ".data"
        account_id = "foreign-schema"
        db_file = data_dir / account_id / "mail.db"
        db_file.parent.mkdir(parents=True)

        # Create a valid SQLite DB but with a different schema.
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE unrelated (x INTEGER)")
        conn.commit()
        conn.close()

        caplog.set_level(logging.WARNING)
        discovered = discover_accounts_from_settings_stores(str(data_dir))

        assert discovered == []
        assert "Failed to load settings store" in caplog.text
        assert account_id in caplog.text


# ---------------------------------------------------------------------------
# merge_settings_store_accounts
# ---------------------------------------------------------------------------


class TestMergeSettingsStoreAccounts:
    """Unit tests for the merge function."""

    def test_merges_new_account(
        self, cfg: MailConfig, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A discovered account not already in the config is added."""
        discovered_db = str(tmp_path / "discovered.db")
        discovered_cfg = MailConfig(
            imap_host="imap.discovered.com",
            smtp_host="smtp.discovered.com",
            username="discovered@example.com",
            password="s3cret",
            db_path=discovered_db,
        )
        discovered = MailAccount(account_id="discovered", config=discovered_cfg)
        existing = MailAccountsConfig(
            accounts=[MailAccount(account_id="existing", config=cfg)],
        )

        with mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[discovered],
        ):
            caplog.set_level(logging.INFO)
            result = merge_settings_store_accounts(existing)

        assert len(result.accounts) == 2
        ids = result.ids()
        assert "existing" in ids
        assert "discovered" in ids

        # The discovered account is preserved as-is.
        discovered_in_result = result.get("discovered")
        assert discovered_in_result.config.db_path == discovered_db
        assert discovered_in_result.config.imap_host == "imap.discovered.com"

        assert "Merging 1 account" in caplog.text

    def test_dedups_existing_ids(
        self, cfg: MailConfig, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An already-present account_id is not duplicated."""
        existing = MailAccountsConfig(
            accounts=[MailAccount(account_id="existing", config=cfg)],
        )
        # Discover an account with the same id — should be ignored.
        discovered = MailAccount(
            account_id="existing",
            config=MailConfig(
                imap_host="imap.other.com",
                smtp_host="smtp.other.com",
                username="other@example.com",
                password="otherpass",
            ),
        )

        with mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[discovered],
        ):
            result = merge_settings_store_accounts(existing)

        # Still exactly one account, and it's the originally configured one.
        assert len(result.accounts) == 1
        assert result.accounts[0].config.imap_host == cfg.imap_host
        # No merge log line emitted when nothing new is added.
        assert "Merging" not in caplog.text

    def test_no_discovered_accounts_is_identity(self, cfg: MailConfig) -> None:
        """When discovery returns nothing, the original config is returned
        unchanged (same object, not a copy)."""
        existing = MailAccountsConfig(
            accounts=[MailAccount(account_id="existing", config=cfg)],
        )

        with mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[],
        ):
            result = merge_settings_store_accounts(existing)

        assert result is existing


# ---------------------------------------------------------------------------
# _cmd_serve merge integration
# ---------------------------------------------------------------------------


class TestCmdServeMergeIntegration:
    """Test that _cmd_serve correctly handles a post-merge accounts config."""

    def test_uses_first_account_for_startup_when_no_account_id(
        self, cfg: MailConfig
    ) -> None:
        """When account_id is empty, the first account (in configured order)
        drives server startup via make_board_handler."""
        from robotsix_auto_mail.cli.commands_serve import _cmd_serve

        accounts = MailAccountsConfig(
            accounts=[
                MailAccount(account_id="first", config=cfg),
                MailAccount(
                    account_id="second",
                    config=MailConfig(
                        imap_host="imap.second.com",
                        smtp_host="smtp.second.com",
                        username="second@example.com",
                        password="s3cret",
                    ),
                ),
            ],
        )

        mock_handler_class = mock.MagicMock()
        mock_server = mock.MagicMock()

        with (
            mock.patch(
                "robotsix_auto_mail.server.make_board_handler",
                return_value=mock_handler_class,
            ) as mock_make_handler,
            mock.patch(
                "http.server.ThreadingHTTPServer",
                return_value=mock_server,
            ),
            mock.patch(
                "robotsix_auto_mail.cli.commands_serve._clear_stale_triage_state",
            ),
            mock.patch(
                "robotsix_auto_mail.cli.commands_serve._reconcile_loop",
            ),
            mock.patch(
                "robotsix_auto_mail.cli.commands_serve.threading.Thread",
            ),
        ):
            _cmd_serve(accounts, account_id="", port=8099)

        # make_board_handler received the first account's db_path and config.
        # db_path is positional; mail_config and accounts are keyword.
        args, kwargs = mock_make_handler.call_args
        assert args[0] == cfg.db_path
        assert kwargs["mail_config"] is cfg
        assert kwargs["accounts"] is accounts

    def test_uses_named_account_for_startup(
        self, cfg: MailConfig, tmp_path: Path
    ) -> None:
        """When account_id names a valid account, that account drives startup."""
        from robotsix_auto_mail.cli.commands_serve import _cmd_serve

        second_db = str(tmp_path / "second.db")
        second_cfg = MailConfig(
            imap_host="imap.second.com",
            smtp_host="smtp.second.com",
            username="second@example.com",
            password="s3cret",
            db_path=second_db,
        )
        accounts = MailAccountsConfig(
            accounts=[
                MailAccount(account_id="first", config=cfg),
                MailAccount(account_id="second", config=second_cfg),
            ],
        )

        mock_handler_class = mock.MagicMock()
        mock_server = mock.MagicMock()

        with (
            mock.patch(
                "robotsix_auto_mail.server.make_board_handler",
                return_value=mock_handler_class,
            ) as mock_make_handler,
            mock.patch(
                "http.server.ThreadingHTTPServer",
                return_value=mock_server,
            ),
            mock.patch(
                "robotsix_auto_mail.cli.commands_serve._clear_stale_triage_state",
            ),
            mock.patch(
                "robotsix_auto_mail.cli.commands_serve._reconcile_loop",
            ),
            mock.patch(
                "robotsix_auto_mail.cli.commands_serve.threading.Thread",
            ),
        ):
            _cmd_serve(accounts, account_id="second", port=8099)

        args, kwargs = mock_make_handler.call_args
        assert args[0] == second_db
        assert kwargs["mail_config"] is second_cfg

    def test_unknown_account_id_falls_back_to_first(self, cfg: MailConfig) -> None:
        """When account_id doesn't match any account, the first account is used."""
        from robotsix_auto_mail.cli.commands_serve import _cmd_serve

        accounts = MailAccountsConfig(
            accounts=[MailAccount(account_id="only", config=cfg)],
        )

        mock_handler_class = mock.MagicMock()
        mock_server = mock.MagicMock()

        with (
            mock.patch(
                "robotsix_auto_mail.server.make_board_handler",
                return_value=mock_handler_class,
            ) as mock_make_handler,
            mock.patch(
                "http.server.ThreadingHTTPServer",
                return_value=mock_server,
            ),
            mock.patch(
                "robotsix_auto_mail.cli.commands_serve._clear_stale_triage_state",
            ),
            mock.patch(
                "robotsix_auto_mail.cli.commands_serve._reconcile_loop",
            ),
            mock.patch(
                "robotsix_auto_mail.cli.commands_serve.threading.Thread",
            ),
        ):
            _cmd_serve(accounts, account_id="nonexistent", port=8099)

        args, kwargs = mock_make_handler.call_args
        assert args[0] == cfg.db_path
        assert kwargs["mail_config"] is cfg

    def test_empty_accounts_starts_with_memory_db(self) -> None:
        """When no accounts are configured, the server starts with an
        in-memory database and no mail_config."""
        from robotsix_auto_mail.cli.commands_serve import _cmd_serve

        accounts = MailAccountsConfig(accounts=[])

        mock_handler_class = mock.MagicMock()
        mock_server = mock.MagicMock()

        with (
            mock.patch(
                "robotsix_auto_mail.server.make_board_handler",
                return_value=mock_handler_class,
            ) as mock_make_handler,
            mock.patch(
                "http.server.ThreadingHTTPServer",
                return_value=mock_server,
            ),
            mock.patch(
                "robotsix_auto_mail.cli.commands_serve._clear_stale_triage_state",
            ),
            mock.patch(
                "robotsix_auto_mail.cli.commands_serve._reconcile_loop",
            ),
            mock.patch(
                "robotsix_auto_mail.cli.commands_serve.threading.Thread",
            ),
        ):
            _cmd_serve(accounts, account_id="", port=8099)

        args, kwargs = mock_make_handler.call_args
        assert args[0] == ":memory:"
        assert kwargs["mail_config"] is None
