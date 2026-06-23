"""Tests for load(), ConfigurationError, and auth password handling."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.config import ConfigurationError, MailConfig, load

# ---------------------------------------------------------------------------
# load() convenience function
# ---------------------------------------------------------------------------


def test_load_env_only() -> None:
    """load() with all env vars set returns env config (no TOML needed)."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.env.com",
        "MAIL_SMTP_HOST": "smtp.env.com",
        "MAIL_USERNAME": "env_user",
        "MAIL_PASSWORD": "env_pass",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = load()
        assert cfg.imap_host == "imap.env.com"
        assert cfg.smtp_host == "smtp.env.com"
        assert cfg.username == "env_user"
        assert cfg.password == "env_pass"


def test_load_missing_config_file() -> None:
    """No env vars AND no config file → ConfigurationError."""
    env: dict[str, str] = {
        "MAIL_CONFIG_PATH": "/nonexistent/path/mail.yaml",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError):
            load()


# ---------------------------------------------------------------------------
# ConfigurationError
# ---------------------------------------------------------------------------


def test_load_re_raises_on_invalid_value_not_missing(tmp_path: Path) -> None:
    """load() must NOT fall back to the file when env has an invalid value.

    If from_env() fails because of an invalid value (e.g. a non-integer
    port), the user explicitly set the env var — falling back to the
    file would silently swallow their typo.
    """
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.file.com

smtp:
  host: smtp.file.com

auth:
  username: file_user
  password: file_pass
"""
    )
    env: dict[str, str] = {
        "MAIL_CONFIG_PATH": str(yaml_file),
        "MAIL_IMAP_HOST": "imap.env.com",
        "MAIL_SMTP_HOST": "smtp.env.com",
        "MAIL_USERNAME": "env_user",
        "MAIL_PASSWORD": "env_pass",
        "MAIL_IMAP_PORT": "not-a-number",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            load()
        msg = str(exc.value)
        assert "MAIL_IMAP_PORT" in msg
        assert "not-a-number" in msg


def test_load_re_raises_on_invalid_tls_not_missing(tmp_path: Path) -> None:
    """load() must re-raise when TLS mode is invalid, even if all
    required fields are present."""
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.file.com

smtp:
  host: smtp.file.com

auth:
  username: file_user
  password: file_pass
"""
    )
    env: dict[str, str] = {
        "MAIL_CONFIG_PATH": str(yaml_file),
        "MAIL_IMAP_HOST": "imap.env.com",
        "MAIL_SMTP_HOST": "smtp.env.com",
        "MAIL_USERNAME": "env_user",
        "MAIL_PASSWORD": "env_pass",
        "MAIL_IMAP_TLS_MODE": "tls-9.9",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            load()
        msg = str(exc.value)
        assert "MAIL_IMAP_TLS_MODE" in msg
        assert "tls-9.9" in msg


def test_configuration_error_is_exception() -> None:
    """ConfigurationError is a proper Exception subclass."""
    err = ConfigurationError("test message")
    assert isinstance(err, Exception)
    assert str(err) == "test message"
    assert err.message == "test message"


def test_configuration_error_missing_only_default() -> None:
    """missing_only defaults to False."""
    err = ConfigurationError("test")
    assert err.missing_only is False


def test_configuration_error_missing_only_true() -> None:
    """missing_only can be set to True."""
    err = ConfigurationError("test", missing_only=True)
    assert err.missing_only is True


# ---------------------------------------------------------------------------
# from_yaml: password not required (it can be supplied via MAIL_PASSWORD)
# ---------------------------------------------------------------------------


def test_from_yaml_missing_auth_password_ok(tmp_path: Path) -> None:
    """from_yaml with validate=True does NOT require auth.password."""
    yaml_file = tmp_path / "no_pass.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.example.com

smtp:
  host: smtp.example.com

auth:
  username: user@example.com
"""
    )
    cfg = MailConfig.from_yaml(yaml_file, validate=True)
    assert cfg.password == ""
    assert cfg.username == "user@example.com"


# -- from_env still requires MAIL_PASSWORD --------------------------------


def test_from_env_still_requires_mail_password() -> None:
    """from_env raises ConfigurationError when MAIL_PASSWORD is missing."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "user@example.com",
        # MAIL_PASSWORD intentionally missing
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            MailConfig.from_env()
        msg = str(exc.value)
        assert "MAIL_PASSWORD" in msg
