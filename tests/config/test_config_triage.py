"""Tests for triage-on-ingest configuration setting."""

from __future__ import annotations

from pathlib import Path

from robotsix_auto_mail.config import MailAccountsConfig, MailConfig

# ---------------------------------------------------------------------------
# Triage-on-ingest setting (per-account triage.on_ingest section), parsed
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


def test_triage_on_ingest_default() -> None:
    """triage_on_ingest falls back to True when nothing overrides it."""
    cfg = MailConfig(imap_host="i", smtp_host="s", username="u", password="p")
    assert cfg.triage_on_ingest is True


def test_from_yaml_triage_on_ingest_default(tmp_path: Path) -> None:
    """An account without a triage: section keeps triage_on_ingest True."""
    yaml_file = _write_accounts(tmp_path, "")
    accounts = MailAccountsConfig.from_yaml(yaml_file)
    assert accounts.default.config.triage_on_ingest is True


def test_from_yaml_reads_triage_on_ingest(tmp_path: Path) -> None:
    """from_yaml parses the triage.on_ingest key."""
    yaml_file = _write_accounts(
        tmp_path,
        "    triage:\n      on_ingest: false\n",
    )
    accounts = MailAccountsConfig.from_yaml(yaml_file)
    assert accounts.default.config.triage_on_ingest is False
