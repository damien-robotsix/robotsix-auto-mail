"""Tests for MailConfig.from_env()."""

from __future__ import annotations

import os
from unittest import mock

import pytest

from robotsix_auto_mail.config import ConfigurationError, MailConfig

# ---------------------------------------------------------------------------
# from_env
# ---------------------------------------------------------------------------


def test_from_env_all_required_present() -> None:
    """All required env vars set → valid config."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "user@example.com",
        "MAIL_PASSWORD": "s3cret",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = MailConfig.from_env()
        assert cfg.imap_host == "imap.example.com"
        assert cfg.smtp_host == "smtp.example.com"
        assert cfg.username == "user@example.com"
        assert cfg.password == "s3cret"


def test_from_env_defaults_used_when_absent() -> None:
    """Optional env vars missing → defaults are used."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = MailConfig.from_env()
        assert cfg.imap_port == 993
        assert cfg.imap_tls_mode == "direct-tls"
        assert cfg.smtp_port == 587
        assert cfg.smtp_tls_mode == "starttls"
        assert cfg.imap_folder == "INBOX"


def test_from_env_optional_fields_applied() -> None:
    """All env vars, including optional, are read correctly."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_IMAP_PORT": "143",
        "MAIL_IMAP_TLS_MODE": "starttls",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_SMTP_PORT": "465",
        "MAIL_SMTP_TLS_MODE": "direct-tls",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
        "MAIL_IMAP_FOLDER": "Archive",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = MailConfig.from_env()
        assert cfg.imap_port == 143
        assert cfg.imap_tls_mode == "starttls"
        assert cfg.smtp_port == 465
        assert cfg.smtp_tls_mode == "direct-tls"
        assert cfg.imap_folder == "Archive"


def test_from_env_missing_required_multiple() -> None:
    """Missing multiple required vars → error lists all of them."""
    env: dict[str, str] = {
        "MAIL_SMTP_HOST": "smtp.example.com",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            MailConfig.from_env()
        msg = str(exc.value)
        assert "MAIL_IMAP_HOST" in msg
        assert "MAIL_SMTP_HOST" not in msg  # this one IS set
        assert "MAIL_USERNAME" in msg
        assert "MAIL_PASSWORD" in msg


def test_from_env_missing_all_required() -> None:
    """No env vars at all → error lists every required var."""
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            MailConfig.from_env()
        msg = str(exc.value)
        for key in (
            "MAIL_IMAP_HOST",
            "MAIL_SMTP_HOST",
            "MAIL_USERNAME",
            "MAIL_PASSWORD",
        ):
            assert key in msg


def test_from_env_invalid_port() -> None:
    """Non-integer port → ConfigurationError."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_IMAP_PORT": "not-a-number",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            MailConfig.from_env()
        msg = str(exc.value)
        assert "MAIL_IMAP_PORT" in msg
        assert "not-a-number" in msg


def test_from_env_invalid_tls_mode() -> None:
    """Invalid TLS mode → ConfigurationError."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_IMAP_TLS_MODE": "tls-1.3",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            MailConfig.from_env()
        msg = str(exc.value)
        assert "MAIL_IMAP_TLS_MODE" in msg
        assert "tls-1.3" in msg


def test_from_env_invalid_smtp_tls_mode() -> None:
    """Invalid SMTP TLS mode → ConfigurationError."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_SMTP_TLS_MODE": "nonexistent",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            MailConfig.from_env()
        msg = str(exc.value)
        assert "MAIL_SMTP_TLS_MODE" in msg


def test_from_env_logging_fields_defaults() -> None:
    """Logging fields fall back to defaults when env vars are absent."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = MailConfig.from_env()
        assert cfg.log_level == "INFO"
        assert cfg.log_format == "console"
        assert cfg.log_file_dir == ".mail_log"


def test_from_env_logging_fields_applied() -> None:
    """LOG_LEVEL, LOG_FORMAT, LOG_FILE_DIR are read from env."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
        "LOG_LEVEL": "DEBUG",
        "LOG_FORMAT": "json",
        "LOG_FILE_DIR": "/var/log/mail",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = MailConfig.from_env()
        assert cfg.log_level == "DEBUG"
        assert cfg.log_format == "json"
        assert cfg.log_file_dir == "/var/log/mail"


def test_from_env_invalid_log_level() -> None:
    """Invalid LOG_LEVEL → ConfigurationError."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
        "LOG_LEVEL": "FATAL",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            MailConfig.from_env()
        msg = str(exc.value)
        assert "LOG_LEVEL" in msg


def test_from_env_invalid_log_format() -> None:
    """Invalid LOG_FORMAT → ConfigurationError."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
        "LOG_FORMAT": "xml",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            MailConfig.from_env()
        msg = str(exc.value)
        assert "LOG_FORMAT" in msg


def test_from_env_microsoft_oauth2_skips_password_requirement() -> None:
    """With MAIL_OAUTH2_PROVIDER=microsoft, MAIL_PASSWORD is not required."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "outlook.office365.com",
        "MAIL_SMTP_HOST": "smtp.office365.com",
        "MAIL_USERNAME": "user@contoso.com",
        "MAIL_OAUTH2_PROVIDER": "microsoft",
        # MAIL_PASSWORD intentionally omitted
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = MailConfig.from_env()
        assert cfg.oauth2_provider == "microsoft"
        assert cfg.password == ""
