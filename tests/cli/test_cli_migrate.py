"""Tests for the CLI migrate-config subcommand."""

from __future__ import annotations

from pathlib import Path

import pytest

from robotsix_auto_mail.cli import main
from robotsix_auto_mail.config import MailAccountsConfig

# ---------------------------------------------------------------------------
# migrate-config
# ---------------------------------------------------------------------------


_MONO_CONFIG = (
    "imap:\n  host: imap.example.com\n  port: 1993\n"
    "smtp:\n  host: smtp.example.com\n"
    'auth:\n  username: u@example.com\n  password: "s3cret"\n'
    "llm:\n  api_key: sk-keep\n"
    "langfuse:\n  public_key: pk-lf-keep\n"
    "  secret_key: sk-lf-keep\n"
    "  base_url: https://cloud.langfuse.com\n"
)


def test_migrate_config_converts_mono_and_writes_backup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """migrate-config rewrites a mono file to accounts shape, preserving values."""
    cfg = tmp_path / "mail.local.yaml"
    cfg.write_text(_MONO_CONFIG)

    rc = main(["migrate-config", "--config", str(cfg)])

    assert rc == 0
    backup = tmp_path / "mail.local.yaml.bak"
    assert backup.exists()
    assert backup.read_text() == _MONO_CONFIG
    migrated = MailAccountsConfig.from_yaml(str(cfg))
    assert migrated.ids() == ("default",)
    acct = migrated.default.config
    assert acct.imap_host == "imap.example.com"
    assert acct.imap_port == 1993
    assert acct.password == "s3cret"
    assert acct.llm_api_key == "sk-keep"
    assert acct.langfuse_public_key == "pk-lf-keep"
    assert acct.langfuse_secret_key == "sk-lf-keep"
    assert acct.langfuse_base_url == "https://cloud.langfuse.com"
    assert acct.db_path == ".data/default/mail.db"

    # Verify top-level llm: / langfuse: sections in the rendered YAML.
    rendered = cfg.read_text()
    llm_pos = rendered.index("llm:")
    langfuse_pos = rendered.index("langfuse:")
    accts_pos = rendered.index("accounts:")
    assert llm_pos < accts_pos, "llm: must be top-level (before accounts:)"
    assert langfuse_pos < accts_pos, "langfuse: must be top-level (before accounts:)"
    # Only one llm: / langfuse: block (top-level; none per-account).
    assert rendered.count("llm:") == 1
    assert rendered.count("langfuse:") == 1


def test_migrate_config_custom_id(tmp_path: Path) -> None:
    """migrate-config --id sets the migrated account id and store folder."""
    cfg = tmp_path / "mail.local.yaml"
    cfg.write_text(_MONO_CONFIG)

    rc = main(["migrate-config", "--config", str(cfg), "--id", "personal"])

    assert rc == 0
    migrated = MailAccountsConfig.from_yaml(str(cfg))
    assert migrated.ids() == ("personal",)
    assert migrated.default.config.db_path == ".data/personal/mail.db"


def test_migrate_config_idempotent_on_multi(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """migrate-config is a no-op (exit 0) on an already-multi file."""
    cfg = tmp_path / "mail.local.yaml"
    multi = (
        "default_account: a\naccounts:\n  - id: a\n"
        "    imap:\n      host: i\n    smtp:\n      host: s\n"
        '    auth:\n      username: u\n      password: "p"\n'
        "    store:\n      path: .data/a/mail.db\n"
    )
    cfg.write_text(multi)

    rc = main(["migrate-config", "--config", str(cfg)])

    assert rc == 0
    assert "already" in capsys.readouterr().out.lower()
    assert cfg.read_text() == multi
    assert not (tmp_path / "mail.local.yaml.bak").exists()


def test_migrate_config_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """migrate-config errors (exit 1) on a missing file."""
    rc = main(["migrate-config", "--config", str(tmp_path / "nope.yaml")])

    assert rc == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_migrate_config_dry_run_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--dry-run prints the migrated YAML without writing the file or backup."""
    cfg = tmp_path / "mail.local.yaml"
    cfg.write_text(_MONO_CONFIG)

    rc = main(["migrate-config", "--config", str(cfg), "--dry-run"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "accounts:" in out
    assert "default_account:" in out
    # Top-level llm: / langfuse: sections appear before accounts:
    llm_pos = out.index("llm:")
    langfuse_pos = out.index("langfuse:")
    accts_pos = out.index("accounts:")
    assert llm_pos < accts_pos, "llm: must be top-level (before accounts:)"
    assert langfuse_pos < accts_pos, "langfuse: must be top-level (before accounts:)"
    assert cfg.read_text() == _MONO_CONFIG
    assert not (tmp_path / "mail.local.yaml.bak").exists()
