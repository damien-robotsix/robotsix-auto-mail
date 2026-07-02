"""Unit tests for the config loader module (loader.py).

Configuration is read exclusively from the YAML file located by
``MAIL_CONFIG_PATH`` (default ``config/mail.local.yaml``); the environment
only *locates* the file.  Covers load(), load_accounts(), load_llm(),
load_llm_provider_model(), resolve_llm_api_key() and
resolve_llm_provider_model().
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from robotsix_auto_mail.config import (
    ConfigurationError,
    MailAccountsConfig,
    MailConfig,
    load,
    load_accounts,
    load_llm,
    load_llm_provider_model,
    resolve_llm_api_key,
    resolve_llm_provider_model,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _multi_account_yaml(tmp_path: Path, **overrides: str) -> Path:
    """Write a minimal ``accounts:`` YAML file and return its path.

    Optional ``llm_api_key`` / ``llm_provider_model`` overrides emit a
    top-level ``llm:`` section.
    """
    yaml_file = tmp_path / "mail.local.yaml"
    llm_api_key = overrides.pop("llm_api_key", None)
    llm_provider_model = overrides.pop("llm_provider_model", None)
    lines: list[str] = []
    if llm_api_key or llm_provider_model:
        lines.append("llm:")
        if llm_api_key:
            lines.append(f"  api_key: {llm_api_key}")
        if llm_provider_model:
            lines.append(f"  provider_model: {llm_provider_model}")

    lines += [
        "accounts:",
        "  - id: default",
        "    imap:",
        "      host: imap.example.com",
        "    smtp:",
        "      host: smtp.example.com",
        "    auth:",
        "      username: user@example.com",
        "      password: pass",
    ]
    yaml_file.write_text("\n".join(lines) + "\n")
    return yaml_file


def _mono_yaml(tmp_path: Path) -> Path:
    """Write a legacy single-account (mono) YAML file (no ``accounts:`` list)."""
    yaml_file = tmp_path / "mono.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.example.com
smtp:
  host: smtp.example.com
auth:
  username: user@example.com
  password: pass
"""
    )
    return yaml_file


# ---------------------------------------------------------------------------
# load_llm()
# ---------------------------------------------------------------------------


def test_load_llm_reads_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """load_llm reads llm.api_key from the located YAML file."""
    yaml_file = _multi_account_yaml(tmp_path, llm_api_key="sk-from-file")
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(yaml_file))
    assert load_llm() == "sk-from-file"


def test_load_llm_yaml_without_llm_section(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A YAML file without a top-level llm: section yields empty string."""
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(_multi_account_yaml(tmp_path)))
    assert load_llm() == ""


def test_load_llm_missing_config_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """When MAIL_CONFIG_PATH points to a missing file, load_llm returns ''."""
    monkeypatch.setenv("MAIL_CONFIG_PATH", "/nonexistent/mail.yaml")
    assert load_llm() == ""


def test_load_llm_default_config_path_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With MAIL_CONFIG_PATH unset and no default file present, returns ''."""
    monkeypatch.delenv("MAIL_CONFIG_PATH", raising=False)
    assert load_llm() == ""


# ---------------------------------------------------------------------------
# load_llm_provider_model()
# ---------------------------------------------------------------------------


def test_load_llm_provider_model_reads_yaml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """load_llm_provider_model reads llm.provider_model from the YAML file."""
    yaml_file = _multi_account_yaml(tmp_path, llm_provider_model="yaml-model")
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(yaml_file))
    assert load_llm_provider_model() == "yaml-model"


def test_load_llm_provider_model_yaml_without_llm_section(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A YAML file without an llm: section → empty string."""
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(_multi_account_yaml(tmp_path)))
    assert load_llm_provider_model() == ""


def test_load_llm_provider_model_yaml_without_provider_model_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """llm: section present but without provider_model → empty string."""
    yaml_file = _multi_account_yaml(tmp_path, llm_api_key="k")
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(yaml_file))
    assert load_llm_provider_model() == ""


# ---------------------------------------------------------------------------
# resolve_llm_api_key()
# ---------------------------------------------------------------------------


def test_resolve_llm_api_key_explicit_arg_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit api_key argument is the top priority."""
    monkeypatch.delenv("MAIL_CONFIG_PATH", raising=False)
    assert resolve_llm_api_key("explicit-key") == "explicit-key"


def test_resolve_llm_api_key_falls_back_to_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No arg → falls back to the config file's llm.api_key."""
    yaml_file = _multi_account_yaml(tmp_path, llm_api_key="sk-from-file")
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(yaml_file))
    assert resolve_llm_api_key() == "sk-from-file"


def test_resolve_llm_api_key_raise_on_missing_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """raise_on_missing=True and no key anywhere → ConfigurationError."""
    monkeypatch.setenv("MAIL_CONFIG_PATH", "/nonexistent/mail.yaml")
    with pytest.raises(ConfigurationError, match="No LLM API key found"):
        resolve_llm_api_key()


def test_resolve_llm_api_key_raise_on_missing_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """raise_on_missing=False and no key anywhere → empty string."""
    monkeypatch.setenv("MAIL_CONFIG_PATH", "/nonexistent/mail.yaml")
    assert resolve_llm_api_key(raise_on_missing=False) == ""


def test_resolve_llm_api_key_explicit_empty_string_falls_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicit empty string falls through to the file, just like None."""
    yaml_file = _multi_account_yaml(tmp_path, llm_api_key="sk-from-file")
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(yaml_file))
    assert resolve_llm_api_key("") == "sk-from-file"


def test_resolve_llm_api_key_env_var_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no explicit arg, LLM_API_KEY env var is picked up."""
    monkeypatch.delenv("MAIL_CONFIG_PATH", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    assert resolve_llm_api_key() == "env-key"


def test_resolve_llm_api_key_explicit_wins_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit api_key arg wins over the LLM_API_KEY env var."""
    monkeypatch.delenv("MAIL_CONFIG_PATH", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    assert resolve_llm_api_key("explicit-key") == "explicit-key"


# ---------------------------------------------------------------------------
# resolve_llm_provider_model()
# ---------------------------------------------------------------------------


def test_resolve_llm_provider_model_explicit_arg_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit provider_model argument is the top priority."""
    monkeypatch.delenv("MAIL_CONFIG_PATH", raising=False)
    assert resolve_llm_provider_model("explicit-model") == "explicit-model"


def test_resolve_llm_provider_model_falls_back_to_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No arg → falls back to the config file's llm.provider_model."""
    yaml_file = _multi_account_yaml(tmp_path, llm_provider_model="yaml-model")
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(yaml_file))
    assert resolve_llm_provider_model() == "yaml-model"


def test_resolve_llm_provider_model_caller_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When nothing is configured, the caller-supplied default is used."""
    monkeypatch.setenv("MAIL_CONFIG_PATH", "/nonexistent/mail.yaml")
    assert resolve_llm_provider_model(default="my-default") == "my-default"


def test_resolve_llm_provider_model_explicit_empty_falls_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Empty string arg falls through to the file/default."""
    yaml_file = _multi_account_yaml(tmp_path, llm_provider_model="yaml-model")
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(yaml_file))
    assert resolve_llm_provider_model("") == "yaml-model"


def test_resolve_llm_provider_model_env_var_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no explicit arg, LLM_PROVIDER_MODEL env var is picked up."""
    monkeypatch.delenv("MAIL_CONFIG_PATH", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_MODEL", "env-model")
    assert resolve_llm_provider_model() == "env-model"


def test_resolve_llm_provider_model_explicit_wins_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit provider_model arg wins over the LLM_PROVIDER_MODEL env var."""
    monkeypatch.delenv("MAIL_CONFIG_PATH", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_MODEL", "env-model")
    assert resolve_llm_provider_model("explicit-model") == "explicit-model"


# ---------------------------------------------------------------------------
# load_accounts()
# ---------------------------------------------------------------------------


def test_load_accounts_reads_yaml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """load_accounts returns the config parsed from the located YAML file."""
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(_multi_account_yaml(tmp_path)))
    accounts = load_accounts()
    assert isinstance(accounts, MailAccountsConfig)
    cfg = accounts.default.config
    assert cfg.imap_host == "imap.example.com"
    assert cfg.username == "user@example.com"


def test_load_accounts_missing_file_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing config file → ConfigurationError."""
    monkeypatch.setenv("MAIL_CONFIG_PATH", "/nonexistent/mail.yaml")
    with pytest.raises(ConfigurationError):
        load_accounts()


def test_load_accounts_no_path_and_no_default_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MAIL_CONFIG_PATH unset AND no default file → ConfigurationError."""
    monkeypatch.delenv("MAIL_CONFIG_PATH", raising=False)
    with pytest.raises(ConfigurationError):
        load_accounts()


def test_load_accounts_mono_yaml_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A mono-shaped YAML file raises with the detect / single-account message."""
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(_mono_yaml(tmp_path)))
    with pytest.raises(ConfigurationError) as exc:
        load_accounts()
    msg = str(exc.value)
    assert "single-account" in msg
    assert "detect" in msg
    assert "migrate-config" not in msg


def test_load_accounts_no_accounts_key_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A YAML file without an 'accounts' key (empty doc) raises (mono shape)."""
    yaml_file = tmp_path / "empty.yaml"
    yaml_file.write_text("{}\n")
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(yaml_file))
    with pytest.raises(ConfigurationError):
        load_accounts()


def test_load_accounts_invalid_yaml_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A syntactically invalid YAML file raises ConfigurationError."""
    yaml_file = tmp_path / "bad.yaml"
    yaml_file.write_text(": invalid yaml\n")
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(yaml_file))
    with pytest.raises(ConfigurationError, match="Invalid YAML"):
        load_accounts()


def test_load_accounts_list_root_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A YAML file whose top-level value is a list raises ConfigurationError."""
    yaml_file = tmp_path / "list.yaml"
    yaml_file.write_text("- item: 1\n")
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(yaml_file))
    with pytest.raises(ConfigurationError):
        load_accounts()


# ---------------------------------------------------------------------------
# load() convenience function
# ---------------------------------------------------------------------------


def test_load_reads_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """load() delegates to load_accounts and returns the default account config."""
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(_multi_account_yaml(tmp_path)))
    cfg = load()
    assert isinstance(cfg, MailConfig)
    assert cfg.imap_host == "imap.example.com"


def test_load_missing_config_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing config file → ConfigurationError."""
    monkeypatch.setenv("MAIL_CONFIG_PATH", "/nonexistent/mail.yaml")
    with pytest.raises(ConfigurationError):
        load()


# ---------------------------------------------------------------------------
# File permission warning (chmod 600 check)
# ---------------------------------------------------------------------------


_PERM_CHMOD_MSG = "chmod 600"


@pytest.mark.skipif(os.getuid() == 0, reason="os.chmod is a no-op under root")
@pytest.mark.parametrize(
    ("file_mode", "expect_warning"),
    [
        (0o644, True),
        (0o600, False),
    ],
)
def test_load_accounts_permission_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    file_mode: int,
    expect_warning: bool,
) -> None:
    """load_accounts warns when the config file has lax group/world permissions."""
    yaml_file = _multi_account_yaml(tmp_path)
    os.chmod(yaml_file, file_mode)
    monkeypatch.setenv("MAIL_CONFIG_PATH", str(yaml_file))

    with caplog.at_level(logging.WARNING, logger="robotsix_auto_mail.config.loader"):
        load_accounts()

    warnings = [r.message for r in caplog.records if _PERM_CHMOD_MSG in r.message]
    if expect_warning:
        assert len(warnings) == 1, f"Expected one warning, got: {warnings}"
        assert str(yaml_file) in warnings[0]
    else:
        assert len(warnings) == 0, f"Expected no warnings, got: {warnings}"
