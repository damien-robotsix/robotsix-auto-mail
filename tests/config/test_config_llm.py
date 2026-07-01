"""Tests for LLM configuration settings (llm_api_key, load_llm)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

from robotsix_auto_mail.config import MailAccountsConfig, MailConfig, load_llm

# ---------------------------------------------------------------------------
# LLM settings (top-level llm: section + LLM_* env vars)
# ---------------------------------------------------------------------------


def test_llm_defaults_when_absent() -> None:
    """llm api key defaults to an empty string."""
    cfg = MailConfig(imap_host="i", smtp_host="s", username="u", password="p")
    assert cfg.llm_api_key == ""


def test_llm_api_key_redacted_in_repr() -> None:
    """repr()/str() must NOT leak the LLM API key."""
    cfg = MailConfig(
        imap_host="i",
        smtp_host="s",
        username="u",
        password="p",
        llm_api_key="sk-or-secret",
    )
    assert "sk-or-secret" not in repr(cfg)
    assert "sk-or-secret" not in str(cfg)
    assert "<redacted>" in repr(cfg)


def test_from_yaml_reads_top_level_llm_section(tmp_path: Path) -> None:
    """from_yaml parses the top-level llm: section onto each account."""
    yaml_file = tmp_path / "accounts.yaml"
    yaml_file.write_text(
        """\
llm:
  api_key: sk-or-from-file
accounts:
  - id: default
    imap:
      host: imap.example.com
    smtp:
      host: smtp.example.com
    auth:
      username: u
      password: p
"""
    )
    accounts = MailAccountsConfig.from_yaml(yaml_file)
    assert accounts.default.config.llm_api_key == "sk-or-from-file"


def test_from_yaml_llm_default_when_absent(tmp_path: Path) -> None:
    """Without a top-level llm: section, llm_api_key defaults to empty."""
    yaml_file = tmp_path / "accounts.yaml"
    yaml_file.write_text(
        """\
accounts:
  - id: default
    imap:
      host: imap.example.com
    smtp:
      host: smtp.example.com
    auth:
      username: u
      password: p
"""
    )
    accounts = MailAccountsConfig.from_yaml(yaml_file)
    assert accounts.default.config.llm_api_key == ""


def test_load_llm_reads_file(tmp_path: Path) -> None:
    """load_llm reads the top-level llm: section from the config file."""
    yaml_file = tmp_path / "mail.local.yaml"
    yaml_file.write_text(
        """\
llm:
  api_key: sk-from-file
accounts:
  - id: default
    imap:
      host: imap.example.com
    smtp:
      host: smtp.example.com
    auth:
      username: u
      password: p
"""
    )
    env: dict[str, str] = {"MAIL_CONFIG_PATH": str(yaml_file)}
    with mock.patch.dict(os.environ, env, clear=True):
        assert load_llm() == "sk-from-file"


def test_load_llm_default_key_when_nothing_set() -> None:
    """load_llm returns an empty key when nothing is set."""
    env: dict[str, str] = {"MAIL_CONFIG_PATH": "/nonexistent/mail.yaml"}
    with mock.patch.dict(os.environ, env, clear=True):
        assert load_llm() == ""
