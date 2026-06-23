"""Tests for triage-on-ingest configuration setting."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

from robotsix_auto_mail.config import MailConfig

# ---------------------------------------------------------------------------
# Triage-on-ingest setting (triage.on_ingest + MAIL_TRIAGE_ON_INGEST)
# ---------------------------------------------------------------------------


def test_triage_on_ingest_default() -> None:
    """triage_on_ingest falls back to True when nothing overrides it."""
    cfg = MailConfig(imap_host="i", smtp_host="s", username="u", password="p")
    assert cfg.triage_on_ingest is True


def test_from_env_reads_triage_on_ingest() -> None:
    """from_env reads a valid MAIL_TRIAGE_ON_INGEST=false as False."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "i",
        "MAIL_SMTP_HOST": "s",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
        "MAIL_TRIAGE_ON_INGEST": "false",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = MailConfig.from_env()
        assert cfg.triage_on_ingest is False


def test_from_yaml_reads_triage_on_ingest(tmp_path: Path) -> None:
    """from_yaml parses the triage.on_ingest key."""
    yaml_file = tmp_path / "triage.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.example.com

smtp:
  host: smtp.example.com

auth:
  username: u
  password: p

triage:
  on_ingest: false
"""
    )
    cfg = MailConfig.from_yaml(yaml_file)
    assert cfg.triage_on_ingest is False
