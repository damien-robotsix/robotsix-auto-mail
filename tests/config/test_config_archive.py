"""Tests for archive configuration settings (archive_root, archive_enabled)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.config import ConfigurationError, MailConfig

# ---------------------------------------------------------------------------
# Archive settings (archive.root / archive.enabled + MAIL_ARCHIVE_* env vars)
# ---------------------------------------------------------------------------


def test_archive_defaults() -> None:
    """archive_root / archive_enabled fall back to their defaults."""
    cfg = MailConfig(imap_host="i", smtp_host="s", username="u", password="p")
    assert cfg.archive_root == "robotsix-mail-archive"
    assert cfg.archive_enabled is True


def test_from_env_reads_archive_fields() -> None:
    """from_env reads MAIL_ARCHIVE_ROOT and a valid MAIL_ARCHIVE_ENABLED."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "i",
        "MAIL_SMTP_HOST": "s",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
        "MAIL_ARCHIVE_ROOT": "custom-archive",
        "MAIL_ARCHIVE_ENABLED": "false",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = MailConfig.from_env()
        assert cfg.archive_root == "custom-archive"
        assert cfg.archive_enabled is False


def test_from_env_invalid_archive_enabled() -> None:
    """A non-boolean MAIL_ARCHIVE_ENABLED → ConfigurationError."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "i",
        "MAIL_SMTP_HOST": "s",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
        "MAIL_ARCHIVE_ENABLED": "maybe",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            MailConfig.from_env()
        msg = str(exc.value)
        assert "MAIL_ARCHIVE_ENABLED" in msg
        assert "maybe" in msg


def test_from_yaml_reads_archive_section(tmp_path: Path) -> None:
    """from_yaml parses the archive.root / archive.enabled keys."""
    yaml_file = tmp_path / "arch.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.example.com

smtp:
  host: smtp.example.com

auth:
  username: u
  password: p

archive:
  root: custom-archive
  enabled: false
"""
    )
    cfg = MailConfig.from_yaml(yaml_file)
    assert cfg.archive_root == "custom-archive"
    assert cfg.archive_enabled is False


def test_from_yaml_invalid_archive_enabled(tmp_path: Path) -> None:
    """A non-bool archive.enabled in YAML → ConfigurationError."""
    yaml_file = tmp_path / "bad_arch.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.example.com

smtp:
  host: smtp.example.com

auth:
  username: u
  password: p

archive:
  enabled: sometimes
"""
    )
    with pytest.raises(ConfigurationError) as exc:
        MailConfig.from_yaml(yaml_file)
    msg = str(exc.value)
    assert "enabled" in msg
