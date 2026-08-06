"""Unit tests for ``discover_accounts_from_settings_stores`` and
``merge_settings_store_accounts`` — the config-recovery path that lets
web-UI-added accounts survive a deploy-system overwrite of
``config/config.json``.

References prior blocked ticket: ``20260803T160419Z-add-unit-tests-for-account-discovery-mer-f132``.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from robotsix_auto_mail.config.model import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.db import init_db
from robotsix_auto_mail.settings.store import (
    SettingsStore,
    discover_accounts_from_settings_stores,
    merge_settings_store_accounts,
)


def _seed_account_db(db_path: str) -> MailConfig:
    """Create a SQLite DB at *db_path*, seed it with a minimal ``MailConfig``,
    and return the config used for seeding.
    """
    conn = init_db(db_path)
    try:
        store = SettingsStore(db_path)
        cfg = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
            password="s3cret",
        )
        store.seed_from_mail_config(conn, cfg)
    finally:
        conn.close()
    return cfg


class TestDiscoverAccountsFromSettingsStores:
    """Tests for ``discover_accounts_from_settings_stores``."""

    def test_discovers_seeded_account(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A seeded settings store is discovered and reconstructed as a
        ``MailAccount`` with the correct ``account_id`` and ``db_path``.
        """
        data_dir = tmp_path / "data"
        account_dir = data_dir / "my-account-1"
        account_dir.mkdir(parents=True)
        db_file = account_dir / "mail.db"
        expected_cfg = _seed_account_db(str(db_file))

        with caplog.at_level(logging.INFO):
            discovered = discover_accounts_from_settings_stores(str(data_dir))

        assert len(discovered) == 1
        account = discovered[0]
        assert account.account_id == "my-account-1"
        assert account.config.imap_host == expected_cfg.imap_host
        assert account.config.smtp_host == expected_cfg.smtp_host
        assert account.config.username == expected_cfg.username
        # db_path must reflect the actual file location, not the seed value.
        assert account.config.db_path == str(db_file)

        # An info log should mention the discovered account.
        log_lines = [r.message for r in caplog.records if r.levelno >= logging.INFO]
        assert any("my-account-1" in line for line in log_lines)

    def test_empty_data_dir_returns_empty(self, tmp_path: Path) -> None:
        """An empty (or non-existent) data directory returns an empty list."""
        data_dir = tmp_path / "empty_data"
        data_dir.mkdir()
        discovered = discover_accounts_from_settings_stores(str(data_dir))
        assert discovered == []

    def test_nonexistent_data_dir_returns_empty(self, tmp_path: Path) -> None:
        """A non-existent data directory returns an empty list."""
        discovered = discover_accounts_from_settings_stores(
            str(tmp_path / "nonexistent")
        )
        assert discovered == []

    def test_non_directory_entries_skipped(self, tmp_path: Path) -> None:
        """Files directly inside the data directory are ignored."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "not-a-dir.txt").write_text("hello")
        discovered = discover_accounts_from_settings_stores(str(data_dir))
        assert discovered == []

    def test_directory_without_db_skipped(self, tmp_path: Path) -> None:
        """Subdirectories without a ``mail.db`` file are skipped."""
        data_dir = tmp_path / "data"
        no_db_dir = data_dir / "no-db-here"
        no_db_dir.mkdir(parents=True)
        discovered = discover_accounts_from_settings_stores(str(data_dir))
        assert discovered == []

    def test_empty_store_skipped(self, tmp_path: Path) -> None:
        """A directory with an unseeded (empty settings) DB is skipped."""
        data_dir = tmp_path / "data"
        account_dir = data_dir / "empty-account"
        account_dir.mkdir(parents=True)
        db_file = account_dir / "mail.db"
        # Create the DB schema but seed nothing.
        conn = init_db(str(db_file))
        conn.close()
        discovered = discover_accounts_from_settings_stores(str(data_dir))
        assert discovered == []

    def test_corrupt_db_skipped_with_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A corrupt (non-SQLite) DB file is skipped with a warning log."""
        data_dir = tmp_path / "data"
        account_dir = data_dir / "corrupt-account"
        account_dir.mkdir(parents=True)
        db_file = account_dir / "mail.db"
        db_file.write_text("this is not a valid SQLite database")

        with caplog.at_level(logging.WARNING):
            discovered = discover_accounts_from_settings_stores(str(data_dir))

        assert discovered == []
        assert any("corrupt-account" in r.message for r in caplog.records)

    def test_missing_required_fields_skipped_with_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A store whose settings are missing required ``MailConfig`` fields
        is skipped with a warning log.
        """
        data_dir = tmp_path / "data"
        account_dir = data_dir / "partial-account"
        account_dir.mkdir(parents=True)
        db_file = account_dir / "mail.db"

        # Seed with a valid config, then manually delete a required field.
        _seed_account_db(str(db_file))
        conn = sqlite3.connect(str(db_file))
        try:
            conn.execute("DELETE FROM component_settings WHERE key = 'imap_host'")
            conn.commit()
        finally:
            conn.close()

        with caplog.at_level(logging.WARNING):
            discovered = discover_accounts_from_settings_stores(str(data_dir))

        # ``to_mail_config`` should return None (not all required fields),
        # so the account is skipped with a warning.
        assert discovered == []
        assert any("partial-account" in r.message for r in caplog.records)

    def test_multiple_accounts_discovered(self, tmp_path: Path) -> None:
        """Multiple seeded accounts in the same data directory are all discovered."""
        data_dir = tmp_path / "data"
        for acct_id in ("acct-a", "acct-b", "acct-c"):
            account_dir = data_dir / acct_id
            account_dir.mkdir(parents=True)
            _seed_account_db(str(account_dir / "mail.db"))

        discovered = discover_accounts_from_settings_stores(str(data_dir))
        discovered_ids = [a.account_id for a in discovered]
        assert discovered_ids == ["acct-a", "acct-b", "acct-c"]


def _make_accounts_config(
    *accounts: MailAccount, default_id: str | None = None
) -> MailAccountsConfig:
    """Build a ``MailAccountsConfig`` with the given accounts and default.

    When *default_id* is ``None`` and *accounts* is non-empty, the first
    account's id is used as the default (required by the model validator).
    """
    accounts_list = list(accounts)
    if default_id is None:
        default_id = accounts_list[0].account_id if accounts_list else ""
    return MailAccountsConfig(accounts=accounts_list, default_account_id=default_id)


def _make_account(account_id: str, db_path: str = "") -> MailAccount:
    """Build a ``MailAccount`` with a minimal ``MailConfig``."""
    cfg = MailConfig(
        imap_host=f"imap.{account_id}.com",
        smtp_host=f"smtp.{account_id}.com",
        username=f"user@{account_id}.com",
        db_path=db_path,
    )
    return MailAccount(account_id=account_id, config=cfg)


class TestMergeSettingsStoreAccounts:
    """Tests for ``merge_settings_store_accounts``."""

    def test_adds_discovered_not_already_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A discovered account not already in the config is added."""
        data_dir = tmp_path / "data"
        account_dir = data_dir / "new-account"
        account_dir.mkdir(parents=True)
        _seed_account_db(str(account_dir / "mail.db"))

        # Override the default data_dir so discovery scans our tmp_path.
        _data_path = str(data_dir)
        monkeypatch.setattr(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            lambda _dir=".data": discover_accounts_from_settings_stores(_data_path),
        )

        existing = _make_accounts_config(_make_account("existing-acct"))
        result = merge_settings_store_accounts(existing)

        assert set(result.ids()) == {"existing-acct", "new-account"}
        # The existing account is unchanged.
        new_acct = result.get("new-account")
        assert new_acct is not None
        assert new_acct.account_id == "new-account"

    def test_skips_already_present_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An already-present account_id is not duplicated."""
        data_dir = tmp_path / "data"
        account_dir = data_dir / "duplicate-acct"
        account_dir.mkdir(parents=True)
        _seed_account_db(str(account_dir / "mail.db"))

        _data_path = str(data_dir)
        monkeypatch.setattr(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            lambda _dir=".data": discover_accounts_from_settings_stores(_data_path),
        )

        existing = _make_accounts_config(_make_account("duplicate-acct"))
        result = merge_settings_store_accounts(existing)

        assert len(result.accounts) == 1
        assert result.ids() == ("duplicate-acct",)

    def test_keeps_existing_default_account_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When a default is already set, it is not overwritten by a merge."""
        data_dir = tmp_path / "data"
        account_dir = data_dir / "discovered-acct"
        account_dir.mkdir(parents=True)
        _seed_account_db(str(account_dir / "mail.db"))

        _data_path = str(data_dir)
        monkeypatch.setattr(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            lambda _dir=".data": discover_accounts_from_settings_stores(_data_path),
        )

        existing = _make_accounts_config(
            _make_account("existing-default"),
            default_id="existing-default",
        )
        result = merge_settings_store_accounts(existing)

        assert result.default_account_id == "existing-default"
        assert set(result.ids()) == {"existing-default", "discovered-acct"}

    def test_promotes_first_discovered_when_no_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the config has no default, the first discovered account in
        the merge becomes the new default.
        """
        data_dir = tmp_path / "data"
        account_dir = data_dir / "first-acct"
        account_dir.mkdir(parents=True)
        _seed_account_db(str(account_dir / "mail.db"))

        _data_path = str(data_dir)
        monkeypatch.setattr(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            lambda _dir=".data": discover_accounts_from_settings_stores(_data_path),
        )

        existing = _make_accounts_config()  # empty, no default
        result = merge_settings_store_accounts(existing)

        assert result.default_account_id == "first-acct"

    def test_keeps_no_default_when_nothing_discovered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When nothing new is discovered and there is no default, nothing changes."""
        monkeypatch.setattr(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            lambda _dir=".data": [],
        )

        existing = _make_accounts_config()
        result = merge_settings_store_accounts(existing)

        assert result.default_account_id == ""
        assert len(result.accounts) == 0

    def test_original_unmutated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The original ``MailAccountsConfig`` is never mutated by the merge."""
        data_dir = tmp_path / "data"
        account_dir = data_dir / "safe-acct"
        account_dir.mkdir(parents=True)
        _seed_account_db(str(account_dir / "mail.db"))

        _data_path = str(data_dir)
        monkeypatch.setattr(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            lambda _dir=".data": discover_accounts_from_settings_stores(_data_path),
        )

        existing = _make_accounts_config(_make_account("original-acct"))
        original_ids = existing.ids()
        result = merge_settings_store_accounts(existing)

        # The result has the new account added.
        assert set(result.ids()) == {"original-acct", "safe-acct"}
        # The original is unchanged.
        assert existing.ids() == original_ids
        assert len(existing.accounts) == 1

    def test_multiple_discovered_merge_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Multiple discovered accounts are all merged, preserving order
        (existing first, then discovered in sorted directory order).
        """
        data_dir = tmp_path / "data"
        for acct_id in ("zzz-last", "aaa-first"):
            account_dir = data_dir / acct_id
            account_dir.mkdir(parents=True)
            _seed_account_db(str(account_dir / "mail.db"))

        _data_path = str(data_dir)
        monkeypatch.setattr(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            lambda _dir=".data": discover_accounts_from_settings_stores(_data_path),
        )

        existing = _make_accounts_config(_make_account("existing-acct"))
        result = merge_settings_store_accounts(existing)

        # Existing accounts come first, then discovered in sorted order.
        assert result.ids() == ("existing-acct", "aaa-first", "zzz-last")
