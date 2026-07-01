"""Tests for ingest interval configuration (ingest_interval_minutes)."""

from __future__ import annotations

from pathlib import Path

from robotsix_auto_mail.config import MailAccountsConfig, MailConfig

# ---------------------------------------------------------------------------
# Ingest interval (per-account ingest.interval_minutes section), parsed
# through MailAccountsConfig.from_yaml.
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


def test_ingest_interval_default() -> None:
    """ingest_interval_minutes defaults to 15."""
    cfg = MailConfig(imap_host="i", smtp_host="s", username="u", password="p")
    assert cfg.ingest_interval_minutes == 15


def test_from_yaml_ingest_interval_default(tmp_path: Path) -> None:
    """An account without an ingest: section keeps the default interval."""
    yaml_file = _write_accounts(tmp_path, "")
    accounts = MailAccountsConfig.from_yaml(yaml_file)
    assert accounts.default.config.ingest_interval_minutes == 15


def test_from_yaml_reads_ingest_interval(tmp_path: Path) -> None:
    """from_yaml parses the ingest.interval_minutes key."""
    yaml_file = _write_accounts(
        tmp_path,
        "    ingest:\n      interval_minutes: 5\n",
    )
    accounts = MailAccountsConfig.from_yaml(yaml_file)
    assert accounts.default.config.ingest_interval_minutes == 5
