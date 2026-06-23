"""Tests for ingest interval configuration (ingest_interval_minutes)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

from robotsix_auto_mail.config import MailConfig

# ---------------------------------------------------------------------------
# Ingest interval (ingest.interval_minutes + MAIL_INGEST_INTERVAL)
# ---------------------------------------------------------------------------


def test_ingest_interval_default() -> None:
    """ingest_interval_minutes defaults to 15."""
    cfg = MailConfig(imap_host="i", smtp_host="s", username="u", password="p")
    assert cfg.ingest_interval_minutes == 15


def test_from_yaml_reads_ingest_interval(tmp_path: Path) -> None:
    """from_yaml parses the ingest.interval_minutes key."""
    yaml_file = tmp_path / "iv.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.example.com

smtp:
  host: smtp.example.com

auth:
  username: u
  password: p

ingest:
  interval_minutes: 5
"""
    )
    cfg = MailConfig.from_yaml(yaml_file)
    assert cfg.ingest_interval_minutes == 5


def test_from_env_reads_ingest_interval() -> None:
    """from_env reads MAIL_INGEST_INTERVAL."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "i",
        "MAIL_SMTP_HOST": "s",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
        "MAIL_INGEST_INTERVAL": "30",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        assert MailConfig.from_env().ingest_interval_minutes == 30
