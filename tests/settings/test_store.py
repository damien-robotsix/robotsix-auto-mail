"""Unit tests for ``SettingsStore`` and module-level helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.config.model import (
    MailAccount,
    MailAccountsConfig,
    MailConfig,
)
from robotsix_auto_mail.db import init_db
from robotsix_auto_mail.settings.store import (
    SettingsStore,
    _is_secret_field,
    _masked_value,
    _validate_field,
    discover_accounts_from_settings_stores,
    merge_settings_store_accounts,
)

# ===========================================================================
# _is_secret_field
# ===========================================================================


@pytest.mark.parametrize(
    "field_name",
    [
        "password",
        "llm_api_key",
        "langfuse_secret_key",
        "oauth2_token",
        "oauth2_client_secret",
        "some_api_key",
        "db_secret",
        "my_token",
    ],
)
def test_is_secret_field_true(field_name: str) -> None:
    """Fields whose name ends with _key, _secret, password, or _token are secret."""
    assert _is_secret_field(field_name) is True


@pytest.mark.parametrize(
    "field_name",
    [
        "imap_host",
        "smtp_host",
        "username",
        "imap_port",
        "db_path",
        "archive_root",
        "ingest_interval_minutes",
    ],
)
def test_is_secret_field_false(field_name: str) -> None:
    """Plain config fields are not secret."""
    assert _is_secret_field(field_name) is False


# ===========================================================================
# _masked_value
# ===========================================================================


def test_masked_value_secret() -> None:
    """Secret fields are masked as '***'."""
    assert _masked_value("password", "my-password") == "***"
    assert _masked_value("llm_api_key", "sk-abc123") == "***"


def test_masked_value_non_secret() -> None:
    """Non-secret fields pass through unchanged."""
    assert _masked_value("imap_host", "imap.example.com") == "imap.example.com"
    assert _masked_value("username", "user@example.com") == "user@example.com"


# ===========================================================================
# _validate_field
# ===========================================================================


def test_validate_field_unknown_rejected() -> None:
    """Unknown fields (not on MailConfig) are rejected."""
    err = _validate_field("nonexistent_field", "value")
    assert err is not None
    assert "unknown setting" in err


def test_validate_field_valid_str() -> None:
    """A known string field with a valid value passes validation."""
    assert _validate_field("imap_host", "imap.example.com") is None
    assert _validate_field("smtp_host", "smtp.example.com") is None
    assert _validate_field("username", "user@example.com") is None


def test_validate_field_valid_int() -> None:
    """Integer fields with valid values pass validation."""
    assert _validate_field("imap_port", "993") is None
    assert _validate_field("smtp_port", "587") is None


def test_validate_field_int_coercion_failure() -> None:
    """Non-numeric values for int fields fail validation."""
    err = _validate_field("imap_port", "not-a-number")
    assert err is not None


def test_validate_field_bool_coercion() -> None:
    """Bool fields accept truthy/falsy string values."""
    assert _validate_field("archive_enabled", "true") is None
    assert _validate_field("triage_on_ingest", "false") is None


def test_validate_field_secret_str_accepts_string() -> None:
    """SecretStr fields accept plain strings."""
    assert _validate_field("password", "s3cret") is None
    assert _validate_field("oauth2_token", "ya29.abc") is None


def test_validate_field_required_field_valid() -> None:
    """A required field (is_required() == True) with a valid value passes.

    This locks the ``info.is_required()`` path — the bug was using
    ``info.default is not ...`` (Ellipsis) which is always True,
    silently mis-validating required fields.
    """
    assert _validate_field("imap_host", "mail.example.com") is None


def test_validate_field_required_field_with_dependent() -> None:
    """Validating a required field succeeds even though other required
    fields (smtp_host, username) are populated with dummy values.

    This confirms ``is_required()`` correctly builds a minimal valid
    config for the Pydantic validation round-trip, isolating the
    target field.
    """
    # ``username`` is required; its validator doesn't check host format.
    # The dummy ``smtp_host`` / ``imap_host`` values must also be valid
    # enough for ``MailConfig.model_validate`` to succeed.
    assert _validate_field("username", "someone@example.org") is None


def test_validate_field_imap_tls_mode_invalid() -> None:
    """imap_tls_mode with an invalid value fails its field_validator."""
    err = _validate_field("imap_tls_mode", "INVALID")
    assert err is not None


def test_validate_field_log_level_invalid() -> None:
    """log_level with an invalid value fails its field_validator."""
    err = _validate_field("log_level", "TRACE")
    assert err is not None


# ===========================================================================
# SettingsStore — CRUD
# ===========================================================================


@pytest.fixture
def conn() -> sqlite3.Connection:
    """Return an in-memory SQLite connection with the schema initialised."""
    c = init_db(":memory:")
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def store() -> SettingsStore:
    """Return a SettingsStore pointing at a fake path (in-memory DB)."""
    return SettingsStore(":memory:")


class TestSettingsStoreEmpty:
    """Tests for an empty store."""

    def test_is_empty_true(
        self, store: SettingsStore, conn: sqlite3.Connection
    ) -> None:
        assert store.is_empty(conn) is True

    def test_get_all_empty(
        self, store: SettingsStore, conn: sqlite3.Connection
    ) -> None:
        assert store.get_all(conn) == {}

    def test_get_missing_key(
        self, store: SettingsStore, conn: sqlite3.Connection
    ) -> None:
        assert store.get(conn, "any_key") is None

    def test_to_mail_config_returns_none(
        self, store: SettingsStore, conn: sqlite3.Connection
    ) -> None:
        assert store.to_mail_config(conn) is None


class TestSettingsStoreSeeded:
    """Tests for a store with pre-seeded settings."""

    @pytest.fixture
    def seeded_conn(
        self, store: SettingsStore, conn: sqlite3.Connection
    ) -> sqlite3.Connection:
        """Seed the store with a MailConfig, then return the connection."""
        cfg = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
            password="s3cret",
            oauth2_token="ya29.abc123",
        )
        store.seed_from_mail_config(conn, cfg)
        return conn

    def test_is_empty_false(
        self, store: SettingsStore, seeded_conn: sqlite3.Connection
    ) -> None:
        assert store.is_empty(seeded_conn) is False

    def test_get_all_masks_secrets(
        self, store: SettingsStore, seeded_conn: sqlite3.Connection
    ) -> None:
        settings = store.get_all(seeded_conn)
        # Non-secret fields pass through.
        assert settings["imap_host"] == "imap.example.com"
        assert settings["username"] == "user@example.com"
        # Secret fields are masked.
        assert settings["password"] == "***"
        assert settings["oauth2_token"] == "***"

    def test_get_masks_secret(
        self, store: SettingsStore, seeded_conn: sqlite3.Connection
    ) -> None:
        assert store.get(seeded_conn, "imap_host") == "imap.example.com"
        assert store.get(seeded_conn, "password") == "***"

    def test_get_nonexistent_key(
        self, store: SettingsStore, seeded_conn: sqlite3.Connection
    ) -> None:
        assert store.get(seeded_conn, "nonexistent") is None

    def test_update_valid_fields(
        self, store: SettingsStore, seeded_conn: sqlite3.Connection
    ) -> None:
        errors = store.update(
            seeded_conn, {"imap_host": "new.example.com", "imap_port": "143"}
        )
        assert errors == {}
        assert store.get(seeded_conn, "imap_host") == "new.example.com"
        assert store.get(seeded_conn, "imap_port") == "143"

    def test_update_unknown_field_rejected(
        self, store: SettingsStore, conn: sqlite3.Connection
    ) -> None:
        errors = store.update(conn, {"bad_field": "value"})
        assert "bad_field" in errors
        assert "unknown setting" in errors["bad_field"]

    def test_update_partial_success(
        self, store: SettingsStore, conn: sqlite3.Connection
    ) -> None:
        """Valid fields are persisted even when other fields fail validation."""
        errors = store.update(
            conn, {"imap_host": "host.example.com", "bad_field": "value"}
        )
        assert "bad_field" in errors
        assert "imap_host" not in errors
        # The valid field was persisted.
        assert store.get(conn, "imap_host") == "host.example.com"

    def test_update_secret_field_stored_raw(
        self, store: SettingsStore, conn: sqlite3.Connection
    ) -> None:
        """Secret fields are stored as plain text but masked on read."""
        store.update(conn, {"password": "new-secret"})
        # Read returns masked value.
        assert store.get(conn, "password") == "***"

    def test_to_mail_config_round_trip(
        self, store: SettingsStore, conn: sqlite3.Connection
    ) -> None:
        """seed_from_mail_config → to_mail_config round-trips faithfully."""
        original = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
            password="s3cret",
            imap_port=993,
            archive_enabled=True,
        )
        store.seed_from_mail_config(conn, original)
        result = store.to_mail_config(conn)
        assert result is not None
        assert result.imap_host == original.imap_host
        assert result.smtp_host == original.smtp_host
        assert result.username == original.username
        assert result.imap_port == original.imap_port
        assert result.archive_enabled == original.archive_enabled
        # Secret fields round-trip correctly.
        assert result.password.get_secret_value() == "s3cret"


# ===========================================================================
# discover_accounts_from_settings_stores
# ===========================================================================


def _seed_account_db(db_path: str) -> MailConfig:
    """Create a DB at *db_path* seeded with a minimal valid MailConfig.

    Returns the seeded MailConfig for assertions.
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
        return cfg
    finally:
        conn.close()


class TestDiscoverAccountsFromSettingsStores:
    """Tests for ``discover_accounts_from_settings_stores``."""

    def test_empty_when_data_dir_missing(self, tmp_path: Path) -> None:
        """Non-existent data_dir returns an empty list."""
        result = discover_accounts_from_settings_stores(
            str(tmp_path / "nonexistent")
        )
        assert result == []

    def test_empty_when_no_subdirectories(self, tmp_path: Path) -> None:
        """A data_dir with files but no subdirectories returns empty."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "some_file.txt").touch()
        result = discover_accounts_from_settings_stores(str(data_dir))
        assert result == []

    def test_subdir_without_mail_db_is_skipped(self, tmp_path: Path) -> None:
        """A subdirectory without a mail.db is silently skipped."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "account-1").mkdir()
        result = discover_accounts_from_settings_stores(str(data_dir))
        assert result == []

    def test_empty_store_is_skipped(self, tmp_path: Path) -> None:
        """A mail.db with an empty component_settings table is skipped."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        account_dir = data_dir / "account-1"
        account_dir.mkdir()
        db_path = str(account_dir / "mail.db")
        # Create DB with schema but no settings.
        conn = init_db(db_path)
        conn.close()
        result = discover_accounts_from_settings_stores(str(data_dir))
        assert result == []

    def test_single_valid_account(self, tmp_path: Path) -> None:
        """A subdirectory with a seeded mail.db returns a MailAccount."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        account_dir = data_dir / "myaccount"
        account_dir.mkdir()
        db_path = str(account_dir / "mail.db")
        seeded = _seed_account_db(db_path)

        result = discover_accounts_from_settings_stores(str(data_dir))
        assert len(result) == 1
        account = result[0]
        assert account.account_id == "myaccount"
        assert account.config.imap_host == seeded.imap_host
        assert account.config.smtp_host == seeded.smtp_host
        assert account.config.username == seeded.username
        assert account.config.password.get_secret_value() == "s3cret"
        # db_path is updated to reflect the actual file location.
        assert account.config.db_path == db_path

    def test_multiple_valid_accounts(self, tmp_path: Path) -> None:
        """Multiple subdirectories with valid mail.db files are all returned."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        for name in ("a-account", "b-account", "c-account"):
            account_dir = data_dir / name
            account_dir.mkdir()
            db_path = str(account_dir / "mail.db")
            _seed_account_db(db_path)

        result = discover_accounts_from_settings_stores(str(data_dir))
        assert len(result) == 3
        ids = {a.account_id for a in result}
        assert ids == {"a-account", "b-account", "c-account"}

    def test_corrupt_db_is_skipped(self, tmp_path: Path) -> None:
        """A subdirectory with a corrupt (non-SQLite) mail.db is skipped."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        account_dir = data_dir / "bad-account"
        account_dir.mkdir()
        db_file = account_dir / "mail.db"
        db_file.write_bytes(b"not a valid sqlite database")

        result = discover_accounts_from_settings_stores(str(data_dir))
        assert result == []

    def test_mixed_valid_and_invalid(self, tmp_path: Path) -> None:
        """Only valid accounts are returned; invalid ones are skipped."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        # Valid account.
        good_dir = data_dir / "good-account"
        good_dir.mkdir()
        _seed_account_db(str(good_dir / "mail.db"))

        # Corrupt DB.
        bad_dir = data_dir / "bad-account"
        bad_dir.mkdir()
        (bad_dir / "mail.db").write_bytes(b"garbage")

        # Missing mail.db.
        missing_dir = data_dir / "missing-db"
        missing_dir.mkdir()

        result = discover_accounts_from_settings_stores(str(data_dir))
        assert len(result) == 1
        assert result[0].account_id == "good-account"


# ===========================================================================
# merge_settings_store_accounts
# ===========================================================================


def _make_accounts_config(
    *accounts: MailAccount, default_account_id: str | None = None
) -> MailAccountsConfig:
    """Build a MailAccountsConfig from MailAccount instances."""
    if default_account_id is None:
        default_account_id = accounts[0].account_id if accounts else ""
    return MailAccountsConfig(
        accounts=list(accounts),
        default_account_id=default_account_id,
    )


def _make_account(account_id: str) -> MailAccount:
    """Build a minimal MailAccount for merge tests."""
    return MailAccount(
        account_id=account_id,
        config=MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username=f"{account_id}@example.com",
            password="s3cret",
        ),
    )


class TestMergeSettingsStoreAccounts:
    """Tests for ``merge_settings_store_accounts``."""

    def test_no_discovered_returns_original(self) -> None:
        """When no accounts are discovered, the original is returned unchanged."""
        original = _make_accounts_config(_make_account("existing"))
        with mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[],
        ):
            result = merge_settings_store_accounts(original)
        assert result is original

    def test_discovered_already_present_returns_original(self) -> None:
        """When discovered accounts are already in the config, original returned."""
        existing = _make_account("existing")
        original = _make_accounts_config(existing)
        discovered = [existing]
        with mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=discovered,
        ):
            result = merge_settings_store_accounts(original)
        assert result is original

    def test_new_account_is_merged(self) -> None:
        """A discovered account not in the config is merged in."""
        existing = _make_account("existing")
        original = _make_accounts_config(existing)
        new_account = _make_account("new-account")
        with mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[new_account],
        ):
            result = merge_settings_store_accounts(original)
        assert result is not original
        assert result.ids() == ("existing", "new-account")

    def test_multiple_new_accounts_merged(self) -> None:
        """Multiple new accounts are all merged in."""
        existing = _make_account("existing")
        original = _make_accounts_config(existing)
        new_a = _make_account("new-a")
        new_b = _make_account("new-b")
        with mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[new_a, new_b],
        ):
            result = merge_settings_store_accounts(original)
        assert result.ids() == ("existing", "new-a", "new-b")

    def test_merge_sets_default_when_none(self) -> None:
        """When there is no default and the first account is merged, it becomes default."""
        original = _make_accounts_config()  # empty
        new_account = _make_account("first-account")
        with mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[new_account],
        ):
            result = merge_settings_store_accounts(original)
        assert result.default_account_id == "first-account"

    def test_merge_preserves_existing_default(self) -> None:
        """An existing default_account_id is preserved after merging."""
        existing = _make_account("existing")
        original = _make_accounts_config(existing, default_account_id="existing")
        new_account = _make_account("new-account")
        with mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[new_account],
        ):
            result = merge_settings_store_accounts(original)
        assert result.default_account_id == "existing"

    def test_merge_with_existing_and_new_preserves_order(self) -> None:
        """Existing accounts stay first; new accounts are appended."""
        a1 = _make_account("alpha")
        a2 = _make_account("beta")
        original = _make_accounts_config(a1, a2)
        new_account = _make_account("gamma")
        with mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[new_account],
        ):
            result = merge_settings_store_accounts(original)
        assert result.ids() == ("alpha", "beta", "gamma")
