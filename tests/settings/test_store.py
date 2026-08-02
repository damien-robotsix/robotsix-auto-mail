"""Unit tests for ``SettingsStore`` and module-level helpers."""

from __future__ import annotations

import sqlite3

import pytest

from robotsix_auto_mail.config.model import MailConfig
from robotsix_auto_mail.db import init_db
from robotsix_auto_mail.settings.store import (
    SettingsStore,
    _is_secret_field,
    _masked_value,
    _validate_field,
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
