"""Tests for archive configuration settings (archive_root, archive_enabled)."""

from __future__ import annotations

from pathlib import Path

import pytest

from robotsix_auto_mail.config import ConfigurationError, MailAccountsConfig, MailConfig

# ---------------------------------------------------------------------------
# Archive settings (per-account archive.root / archive.enabled section),
# parsed through MailAccountsConfig.from_yaml.
# ---------------------------------------------------------------------------


def _write_accounts(tmp_path: Path, account_body: str) -> Path:
    """Write a one-entry ``accounts:`` YAML file and return its path."""
    yaml_file = tmp_path / "accounts.yaml"
    yaml_file.write_text(
        "accounts:\n"
        "  - id: default\n"
        "    imap:\n"
        "      host: imap.example.com\n"
        "    smtp:\n"
        "      host: smtp.example.com\n"
        "    auth:\n"
        "      username: u\n"
        "      password: p\n" + account_body
    )
    return yaml_file


def test_archive_defaults() -> None:
    """archive_root / archive_enabled fall back to their defaults."""
    cfg = MailConfig(imap_host="i", smtp_host="s", username="u", password="p")
    assert cfg.archive_root == "robotsix-mail-archive"
    assert cfg.archive_enabled is True


def test_from_yaml_archive_defaults(tmp_path: Path) -> None:
    """An account without an archive: section keeps the archive defaults."""
    yaml_file = _write_accounts(tmp_path, "")
    accounts = MailAccountsConfig.from_yaml(yaml_file)
    cfg = accounts.default.config
    assert cfg.archive_root == "robotsix-mail-archive"
    assert cfg.archive_enabled is True


def test_from_yaml_reads_archive_section(tmp_path: Path) -> None:
    """from_yaml parses the archive.root / archive.enabled keys."""
    yaml_file = _write_accounts(
        tmp_path,
        "    archive:\n      root: custom-archive\n      enabled: false\n",
    )
    accounts = MailAccountsConfig.from_yaml(yaml_file)
    cfg = accounts.default.config
    assert cfg.archive_root == "custom-archive"
    assert cfg.archive_enabled is False


def test_from_yaml_invalid_archive_enabled(tmp_path: Path) -> None:
    """A non-bool archive.enabled in YAML → ConfigurationError."""
    yaml_file = _write_accounts(
        tmp_path,
        "    archive:\n      enabled: sometimes\n",
    )
    with pytest.raises(ConfigurationError) as exc:
        MailAccountsConfig.from_yaml(yaml_file)
    msg = str(exc.value)
    assert "enabled" in msg
    assert "sometimes" in msg
