"""Tests for load(), ConfigurationError, and YAML-file-only config loading.

Environment-variable configuration has been removed: ``load()`` reads
exclusively from the YAML file located by ``MAIL_CONFIG_PATH`` (default
``config/mail.local.yaml``), which must use the ``accounts:`` shape.
``MAIL_CONFIG_PATH`` only *locates* the file — it carries no config values.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from robotsix_auto_mail.config import (
    ConfigurationError,
    MailAccountsConfig,
    MailConfig,
    load,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _accounts_yaml(tmp_path: Path) -> Path:
    """Write a minimal single-entry ``accounts:`` YAML file and return its path."""
    yaml_file = tmp_path / "mail.local.yaml"
    yaml_file.write_text(
        """\
accounts:
  - id: default
    imap:
      host: imap.file.com
    smtp:
      host: smtp.file.com
    auth:
      username: file_user
      password: file_pass
"""
    )
    return yaml_file


# ---------------------------------------------------------------------------
# load() convenience function — YAML file only
# ---------------------------------------------------------------------------


def test_load_reads_yaml_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """load() returns the default account's config from the located YAML file."""
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(_accounts_yaml(tmp_path)))
    cfg = load()
    assert isinstance(cfg, MailConfig)
    assert cfg.imap_host == "imap.file.com"
    assert cfg.smtp_host == "smtp.file.com"
    assert cfg.username == "file_user"
    assert cfg.password == "file_pass"


def test_load_missing_config_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing config file → ConfigurationError."""
    monkeypatch.setenv("MAIL_CONFIG_PATH", "/nonexistent/path/mail.yaml")
    with pytest.raises(ConfigurationError):
        load()


def test_load_mono_shape_file_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A file without an ``accounts:`` list (mono shape) → ConfigurationError."""
    yaml_file = tmp_path / "mono.yaml"
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
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(yaml_file))
    with pytest.raises(ConfigurationError) as exc:
        load()
    msg = str(exc.value)
    assert "single-account" in msg
    assert "detect" in msg


# ---------------------------------------------------------------------------
# ConfigurationError
# ---------------------------------------------------------------------------


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
# auth.password is optional in the YAML file
# ---------------------------------------------------------------------------


def test_from_yaml_missing_auth_password_ok(tmp_path: Path) -> None:
    """A YAML account without auth.password loads with an empty password."""
    yaml_file = tmp_path / "no_pass.yaml"
    yaml_file.write_text(
        """\
accounts:
  - id: default
    imap:
      host: imap.example.com
    smtp:
      host: smtp.example.com
    auth:
      username: user@example.com
"""
    )
    cfg = MailAccountsConfig.from_yaml(yaml_file).default.config
    assert cfg.password == ""
    assert cfg.username == "user@example.com"
