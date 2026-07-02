"""Unit tests for the config schema module — error types, constants, and pydantic model validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from robotsix_auto_mail.config.model import MailConfig
from robotsix_auto_mail.config.schema import (
    ConfigurationError,
    _mono_shape_error,
)

# ---------------------------------------------------------------------------
# _mono_shape_error
# ---------------------------------------------------------------------------


def test_mono_shape_error_contains_path_and_commands() -> None:
    result = _mono_shape_error(Path("/etc/mail/my-config.yaml"))
    assert "/etc/mail/my-config.yaml" in result
    assert "detect" in result
    assert "single-account" in result
    assert "migrate-config" not in result


# ---------------------------------------------------------------------------
# ConfigurationError
# ---------------------------------------------------------------------------


def test_configuration_error_defaults() -> None:
    err = ConfigurationError("bad config")
    assert err.message == "bad config"
    assert err.missing_only is False
    assert str(err) == "bad config"


def test_configuration_error_missing_only_true() -> None:
    err = ConfigurationError("missing fields", missing_only=True)
    assert err.missing_only is True


def test_configuration_error_is_exception() -> None:
    with pytest.raises(ConfigurationError, match="test error"):
        raise ConfigurationError("test error missing")


# ---------------------------------------------------------------------------
# MailConfig pydantic validation
# ---------------------------------------------------------------------------


def test_mailconfig_invalid_imap_tls_mode_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user",
            imap_tls_mode="invalid-mode",
        )


def test_mailconfig_invalid_log_level_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user",
            log_level="TRACE",
        )


def test_mailconfig_imap_port_string_coerces_to_int() -> None:
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user",
        imap_port="42",
    )
    assert isinstance(cfg.imap_port, int)
    assert cfg.imap_port == 42


def test_mailconfig_invalid_archive_enabled_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user",
            archive_enabled="not-a-bool",
        )
