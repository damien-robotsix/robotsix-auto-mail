"""Tests for MailConfig.from_yaml()."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.config import ConfigurationError, MailAccountsConfig, MailConfig

# ---------------------------------------------------------------------------
# from_yaml
# ---------------------------------------------------------------------------


def test_from_yaml_example_file() -> None:
    """The bundled multi-account example is valid and parses correctly."""
    accounts = MailAccountsConfig.from_yaml("docs/config/mail.local.example.yaml")
    cfg = accounts.default.config
    assert cfg.imap_host == "imap.gmail.com"
    assert cfg.imap_port == 993
    assert cfg.imap_tls_mode == "direct-tls"
    assert cfg.smtp_host == "smtp.gmail.com"
    assert cfg.smtp_port == 587
    assert cfg.smtp_tls_mode == "starttls"
    assert cfg.username == "me@gmail.com"
    assert cfg.password == ""
    assert cfg.imap_folder == "INBOX"


def test_from_yaml_defaults_for_missing_fields(tmp_path: Path) -> None:
    """Fields missing from YAML fall back to defaults."""
    yaml_file = tmp_path / "minimal.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.example.com

smtp:
  host: smtp.example.com

auth:
  username: u
  password: p
"""
    )
    cfg = MailConfig.from_yaml(yaml_file)
    assert cfg.imap_port == 993
    assert cfg.imap_tls_mode == "direct-tls"
    assert cfg.smtp_port == 587
    assert cfg.smtp_tls_mode == "starttls"
    assert cfg.imap_folder == "INBOX"


def test_from_yaml_custom_imap_folder(tmp_path: Path) -> None:
    """imap_folder can be set via YAML imap.folder key."""
    yaml_file = tmp_path / "folder.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.example.com
  folder: Archive

smtp:
  host: smtp.example.com

auth:
  username: u
  password: p
"""
    )
    cfg = MailConfig.from_yaml(yaml_file)
    assert cfg.imap_folder == "Archive"


def test_from_yaml_langfuse_section(tmp_path: Path) -> None:
    """A langfuse: YAML section populates the three langfuse fields."""
    yaml_file = tmp_path / "langfuse.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.example.com

smtp:
  host: smtp.example.com

auth:
  username: u
  password: p

langfuse:
  public_key: pk-lf-yaml
  secret_key: sk-lf-yaml
  base_url: https://langfuse.example.net
"""
    )
    cfg = MailConfig.from_yaml(yaml_file)
    assert cfg.langfuse_public_key == "pk-lf-yaml"
    assert cfg.langfuse_secret_key == "sk-lf-yaml"
    assert cfg.langfuse_base_url == "https://langfuse.example.net"


def test_from_yaml_langfuse_defaults_when_absent(tmp_path: Path) -> None:
    """Missing langfuse: section → empty-string defaults."""
    yaml_file = tmp_path / "no_langfuse.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.example.com

smtp:
  host: smtp.example.com

auth:
  username: u
  password: p
"""
    )
    cfg = MailConfig.from_yaml(yaml_file)
    assert cfg.langfuse_public_key == ""
    assert cfg.langfuse_secret_key == ""
    assert cfg.langfuse_base_url == ""


def test_mailconfig_oauth2_provider_defaults() -> None:
    """oauth2_provider/tenant defaults: empty provider, 'organizations' tenant."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="u",
        password="p",
    )
    assert cfg.oauth2_provider == ""
    assert cfg.oauth2_tenant == "organizations"


def test_from_env_oauth2_provider_and_tenant() -> None:
    """MAIL_OAUTH2_PROVIDER / MAIL_OAUTH2_TENANT round-trip from env."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
        "MAIL_OAUTH2_PROVIDER": "microsoft",
        "MAIL_OAUTH2_TENANT": "contoso.onmicrosoft.com",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = MailConfig.from_env()
        assert cfg.oauth2_provider == "microsoft"
        assert cfg.oauth2_tenant == "contoso.onmicrosoft.com"


def test_from_env_oauth2_tenant_defaults_when_absent() -> None:
    """Missing tenant env var → defaults to 'organizations'."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = MailConfig.from_env()
        assert cfg.oauth2_provider == ""
        assert cfg.oauth2_tenant == "organizations"


def test_from_yaml_oauth2_provider_and_tenant(tmp_path: Path) -> None:
    """auth.oauth2_provider / auth.oauth2_tenant round-trip from YAML."""
    yaml_file = tmp_path / "msal.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.example.com

smtp:
  host: smtp.example.com

auth:
  username: u
  password: p
  oauth2_provider: microsoft
  oauth2_tenant: contoso.onmicrosoft.com
"""
    )
    cfg = MailConfig.from_yaml(yaml_file)
    assert cfg.oauth2_provider == "microsoft"
    assert cfg.oauth2_tenant == "contoso.onmicrosoft.com"


def test_from_env_langfuse_vars() -> None:
    """LANGFUSE_* env vars populate the langfuse fields."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
        "LANGFUSE_PUBLIC_KEY": "pk-lf-env",
        "LANGFUSE_SECRET_KEY": "sk-lf-env",
        "LANGFUSE_BASE_URL": "https://langfuse.env.net",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = MailConfig.from_env()
        assert cfg.langfuse_public_key == "pk-lf-env"
        assert cfg.langfuse_secret_key == "sk-lf-env"
        assert cfg.langfuse_base_url == "https://langfuse.env.net"


def test_from_env_langfuse_defaults_when_absent() -> None:
    """LANGFUSE_* env vars absent → empty-string defaults."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = MailConfig.from_env()
        assert cfg.langfuse_public_key == ""
        assert cfg.langfuse_secret_key == ""
        assert cfg.langfuse_base_url == ""


def test_from_yaml_missing_required_fields(tmp_path: Path) -> None:
    """Missing required YAML fields → ConfigurationError with all names."""
    yaml_file = tmp_path / "bad.yaml"
    yaml_file.write_text(
        """\
imap:
  port: 993

smtp:
  tls_mode: none
"""
    )
    with pytest.raises(ConfigurationError) as exc:
        MailConfig.from_yaml(yaml_file)
    msg = str(exc.value)
    assert "imap.host" in msg
    assert "smtp.host" in msg
    assert "auth.username" in msg
    # auth.password is not required — it can come from the MAIL_PASSWORD env var


def test_from_yaml_invalid_tls_mode(tmp_path: Path) -> None:
    """Invalid TLS mode in YAML → ConfigurationError."""
    yaml_file = tmp_path / "bad_tls.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.example.com
  tls_mode: bad-mode

smtp:
  host: smtp.example.com

auth:
  username: u
  password: p
"""
    )
    with pytest.raises(ConfigurationError) as exc:
        MailConfig.from_yaml(yaml_file)
    msg = str(exc.value)
    assert "imap.tls_mode" in msg
    assert "bad-mode" in msg


def test_from_yaml_malformed_file(tmp_path: Path) -> None:
    """Malformed YAML → ConfigurationError."""
    yaml_file = tmp_path / "malformed.yaml"
    yaml_file.write_text("this: [is not: valid: YAML")
    with pytest.raises(ConfigurationError) as exc:
        MailConfig.from_yaml(yaml_file)
    assert "Invalid YAML" in str(exc.value)


def test_from_yaml_file_not_found(tmp_path: Path) -> None:
    """Missing file → FileNotFoundError (not swallowed)."""
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(FileNotFoundError):
        MailConfig.from_yaml(missing)


def test_from_yaml_wrong_type_for_field(tmp_path: Path) -> None:
    """Field with wrong type (e.g. port as string) → ConfigurationError."""
    yaml_file = tmp_path / "bad_port.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.example.com
  port: not-a-number

smtp:
  host: smtp.example.com

auth:
  username: u
  password: p
"""
    )
    with pytest.raises(ConfigurationError) as exc:
        MailConfig.from_yaml(yaml_file)
    msg = str(exc.value)
    assert "port" in msg


def test_from_yaml_validate_false_skips_required_checks(
    tmp_path: Path,
) -> None:
    """validate=False skips required-field validation (used for defaults)."""
    yaml_file = tmp_path / "defaults.yaml"
    yaml_file.write_text(
        """\
imap:
  host: ""
  port: 993

smtp:
  host: ""

auth:
  username: ""
  password: ""
"""
    )
    # With validate=True (default), missing required fields should error.
    with pytest.raises(ConfigurationError):
        MailConfig.from_yaml(yaml_file, validate=True)

    # With validate=False, it should succeed — defaults loader path.
    cfg = MailConfig.from_yaml(yaml_file, validate=False)
    assert cfg.imap_host == ""
    assert cfg.imap_port == 993
    assert cfg.smtp_host == ""
    assert cfg.username == ""
    assert cfg.password == ""


def test_from_yaml_null_file_produces_defaults(tmp_path: Path) -> None:
    """A YAML file containing only null / empty → defaults (with validate=False)."""
    yaml_file = tmp_path / "null.yaml"
    yaml_file.write_text("")
    cfg = MailConfig.from_yaml(yaml_file, validate=False)
    assert cfg.imap_host == ""
    assert cfg.imap_port == 993
    assert cfg.imap_tls_mode == "direct-tls"
    assert cfg.smtp_host == ""
    assert cfg.smtp_port == 587
    assert cfg.smtp_tls_mode == "starttls"
    assert cfg.username == ""
    assert cfg.password == ""


def test_from_yaml_root_not_mapping(tmp_path: Path) -> None:
    """A YAML file whose root is not a mapping → ConfigurationError."""
    yaml_file = tmp_path / "list.yaml"
    yaml_file.write_text("- item1\n- item2\n")
    with pytest.raises(ConfigurationError) as exc:
        MailConfig.from_yaml(yaml_file)
    assert "mapping" in str(exc.value).lower()
