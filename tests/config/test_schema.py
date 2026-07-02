"""Unit tests for the config schema module — helpers, error types, and validation constants."""

from __future__ import annotations

import pytest

from robotsix_auto_mail.config.schema import (
    _VALID_LOG_LEVELS,
    ConfigurationError,
)

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
# _VALID_LOG_LEVELS — includes CRITICAL
# ---------------------------------------------------------------------------


def test_valid_log_levels_includes_critical() -> None:
    assert "CRITICAL" in _VALID_LOG_LEVELS


def test_valid_log_levels_contains_expected() -> None:
    assert _VALID_LOG_LEVELS == {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
