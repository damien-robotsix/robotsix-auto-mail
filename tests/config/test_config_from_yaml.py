"""Tests for per-account YAML parsing via MailAccountsConfig.from_yaml().

Environment-variable configuration has been removed; configuration is read
exclusively from a YAML file using the ``accounts:`` shape.  The former
mono-file ``MailConfig.from_yaml`` loader is gone, so per-account YAML
parsing is exercised here through :meth:`MailAccountsConfig.from_yaml` with an
``accounts:`` list of a single entry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from robotsix_auto_mail.config import ConfigurationError, MailAccountsConfig, MailConfig

# ---------------------------------------------------------------------------
# Bundled example file
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


# ---------------------------------------------------------------------------
# A single ``accounts:`` entry — full section coverage
# ---------------------------------------------------------------------------


def test_from_yaml_single_entry_all_sections(tmp_path: Path) -> None:
    """One account entry with imap/smtp/auth/store/oauth2/triage sections plus
    top-level llm/langfuse populates every field on ``container.default.config``."""
    yaml_file = tmp_path / "mail.local.yaml"
    yaml_file.write_text(
        """\
llm:
  api_key: sk-top-level
  provider_model: openrouter-deepseek

langfuse:
  public_key: pk-lf-yaml
  secret_key: sk-lf-yaml
  base_url: https://langfuse.example.net

accounts:
  - id: personal
    label: Personal
    imap:
      host: imap.example.com
      port: 143
      tls_mode: starttls
      folder: Archive
    smtp:
      host: smtp.example.com
      port: 465
      tls_mode: direct-tls
    auth:
      username: user@example.com
      password: s3cret
      oauth2_provider: microsoft
      oauth2_tenant: contoso.onmicrosoft.com
    store:
      path: .data/custom/personal.db
    triage:
      on_ingest: false
"""
    )
    container = MailAccountsConfig.from_yaml(yaml_file)
    cfg = container.default.config

    # imap / smtp / auth
    assert cfg.imap_host == "imap.example.com"
    assert cfg.imap_port == 143
    assert cfg.imap_tls_mode == "starttls"
    assert cfg.imap_folder == "Archive"
    assert cfg.smtp_host == "smtp.example.com"
    assert cfg.smtp_port == 465
    assert cfg.smtp_tls_mode == "direct-tls"
    assert cfg.username == "user@example.com"
    assert cfg.password == "s3cret"

    # oauth2
    assert cfg.oauth2_provider == "microsoft"
    assert cfg.oauth2_tenant == "contoso.onmicrosoft.com"

    # store.path is honoured verbatim (no per-account derivation)
    assert cfg.db_path == ".data/custom/personal.db"

    # triage
    assert cfg.triage_on_ingest is False

    # top-level llm / langfuse applied to the account
    assert cfg.llm_api_key == "sk-top-level"
    assert cfg.llm_provider_model == "openrouter-deepseek"
    assert cfg.langfuse_public_key == "pk-lf-yaml"
    assert cfg.langfuse_secret_key == "sk-lf-yaml"
    assert cfg.langfuse_base_url == "https://langfuse.example.net"

    # label round-trips
    assert container.default.label == "Personal"


# ---------------------------------------------------------------------------
# Defaults for absent optional fields
# ---------------------------------------------------------------------------


def _minimal_account_yaml(tmp_path: Path, account_id: str = "acct") -> Path:
    """Write a minimal single-entry ``accounts:`` YAML file and return its path."""
    yaml_file = tmp_path / "minimal.yaml"
    yaml_file.write_text(
        f"""\
accounts:
  - id: {account_id}
    imap:
      host: imap.example.com
    smtp:
      host: smtp.example.com
    auth:
      username: u
      password: p
"""
    )
    return yaml_file


def test_from_yaml_defaults_for_missing_fields(tmp_path: Path) -> None:
    """Fields missing from the entry fall back to their defaults."""
    cfg = MailAccountsConfig.from_yaml(_minimal_account_yaml(tmp_path)).default.config
    assert cfg.imap_port == 993
    assert cfg.imap_tls_mode == "direct-tls"
    assert cfg.smtp_port == 587
    assert cfg.smtp_tls_mode == "starttls"
    assert cfg.imap_folder == "INBOX"
    assert cfg.triage_on_ingest is True


def test_from_yaml_langfuse_defaults_when_absent(tmp_path: Path) -> None:
    """Missing top-level langfuse: section → empty-string defaults."""
    cfg = MailAccountsConfig.from_yaml(_minimal_account_yaml(tmp_path)).default.config
    assert cfg.langfuse_public_key == ""
    assert cfg.langfuse_secret_key == ""
    assert cfg.langfuse_base_url == ""


def test_from_yaml_llm_defaults_when_absent(tmp_path: Path) -> None:
    """Missing top-level llm: section → empty-string defaults."""
    cfg = MailAccountsConfig.from_yaml(_minimal_account_yaml(tmp_path)).default.config
    assert cfg.llm_api_key == ""
    assert cfg.llm_provider_model == ""


def test_from_yaml_oauth2_provider_and_tenant_defaults(tmp_path: Path) -> None:
    """oauth2_provider/tenant default: empty provider, 'organizations' tenant."""
    cfg = MailAccountsConfig.from_yaml(_minimal_account_yaml(tmp_path)).default.config
    assert cfg.oauth2_provider == ""
    assert cfg.oauth2_tenant == "organizations"


# ---------------------------------------------------------------------------
# Per-account db_path derivation (``.data/<id>/mail.db`` when store.path absent)
# ---------------------------------------------------------------------------


def test_from_yaml_db_path_derived_when_store_absent(tmp_path: Path) -> None:
    """When ``store.path`` is absent, db_path is derived from the account id."""
    cfg = MailAccountsConfig.from_yaml(
        _minimal_account_yaml(tmp_path, account_id="personal")
    ).default.config
    assert cfg.db_path == ".data/personal/mail.db"


def test_from_yaml_db_path_honours_store_path(tmp_path: Path) -> None:
    """An explicit ``store.path`` overrides the derived db_path."""
    yaml_file = tmp_path / "store.yaml"
    yaml_file.write_text(
        """\
accounts:
  - id: personal
    imap:
      host: imap.example.com
    smtp:
      host: smtp.example.com
    auth:
      username: u
      password: p
    store:
      path: .data/explicit/personal.db
"""
    )
    cfg = MailAccountsConfig.from_yaml(yaml_file).default.config
    assert cfg.db_path == ".data/explicit/personal.db"


# ---------------------------------------------------------------------------
# Constructing MailConfig directly (defaults)
# ---------------------------------------------------------------------------


def test_mailconfig_direct_construction_defaults() -> None:
    """MailConfig can be constructed directly; db_path default is empty."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="u",
        password="p",
    )
    assert cfg.db_path == ""
    assert cfg.oauth2_provider == ""
    assert cfg.oauth2_tenant == "organizations"


# ---------------------------------------------------------------------------
# Required-field validation
# ---------------------------------------------------------------------------


def test_from_yaml_missing_required_fields(tmp_path: Path) -> None:
    """Missing required fields → ConfigurationError naming the missing paths."""
    yaml_file = tmp_path / "bad.yaml"
    yaml_file.write_text(
        """\
accounts:
  - id: broken
    imap:
      port: 993
    smtp:
      tls_mode: none
"""
    )
    with pytest.raises(ConfigurationError) as exc:
        MailAccountsConfig.from_yaml(yaml_file)
    msg = str(exc.value)
    assert "imap.host" in msg
    assert "smtp.host" in msg
    assert "auth.username" in msg
    # auth.password is not required — it may be supplied out-of-band / OAuth2.


def test_from_yaml_missing_auth_password_ok(tmp_path: Path) -> None:
    """auth.password is optional even with validate=True (default)."""
    yaml_file = tmp_path / "no_pass.yaml"
    yaml_file.write_text(
        """\
accounts:
  - id: personal
    imap:
      host: imap.example.com
    smtp:
      host: smtp.example.com
    auth:
      username: user@example.com
"""
    )
    cfg = MailAccountsConfig.from_yaml(yaml_file).default.config
    assert cfg.password == ""
    assert cfg.username == "user@example.com"


def test_from_yaml_invalid_tls_mode(tmp_path: Path) -> None:
    """Invalid TLS mode → ConfigurationError naming the field and value."""
    yaml_file = tmp_path / "bad_tls.yaml"
    yaml_file.write_text(
        """\
accounts:
  - id: personal
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
        MailAccountsConfig.from_yaml(yaml_file)
    msg = str(exc.value)
    assert "imap.tls_mode" in msg
    assert "bad-mode" in msg


def test_from_yaml_wrong_type_for_field(tmp_path: Path) -> None:
    """A field with the wrong type (port as a string) → ConfigurationError."""
    yaml_file = tmp_path / "bad_port.yaml"
    yaml_file.write_text(
        """\
accounts:
  - id: personal
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
        MailAccountsConfig.from_yaml(yaml_file)
    assert "port" in str(exc.value)


# ---------------------------------------------------------------------------
# validate=False skips required-field checks
# ---------------------------------------------------------------------------


def test_from_yaml_validate_false_skips_required_checks(tmp_path: Path) -> None:
    """validate=False skips required-field validation (defaults-loader path)."""
    yaml_file = tmp_path / "defaults.yaml"
    yaml_file.write_text(
        """\
accounts:
  - id: personal
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
    # validate=True (default) → missing required fields error out.
    with pytest.raises(ConfigurationError):
        MailAccountsConfig.from_yaml(yaml_file, validate=True)

    # validate=False → succeeds with empty strings, db_path still derived.
    cfg = MailAccountsConfig.from_yaml(yaml_file, validate=False).default.config
    assert cfg.imap_host == ""
    assert cfg.imap_port == 993
    assert cfg.smtp_host == ""
    assert cfg.username == ""
    assert cfg.password == ""
    assert cfg.db_path == ".data/personal/mail.db"


# ---------------------------------------------------------------------------
# File-level error handling
# ---------------------------------------------------------------------------


def test_from_yaml_malformed_file(tmp_path: Path) -> None:
    """Malformed YAML → ConfigurationError mentioning 'Invalid YAML'."""
    yaml_file = tmp_path / "malformed.yaml"
    yaml_file.write_text("this: [is not: valid: YAML")
    with pytest.raises(ConfigurationError) as exc:
        MailAccountsConfig.from_yaml(yaml_file)
    assert "Invalid YAML" in str(exc.value)


def test_from_yaml_file_not_found(tmp_path: Path) -> None:
    """Missing file → FileNotFoundError (not swallowed)."""
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(FileNotFoundError):
        MailAccountsConfig.from_yaml(missing)


def test_from_yaml_root_not_mapping(tmp_path: Path) -> None:
    """A YAML file whose root is not an ``accounts:`` mapping → ConfigurationError."""
    yaml_file = tmp_path / "list.yaml"
    yaml_file.write_text("- item1\n- item2\n")
    with pytest.raises(ConfigurationError):
        MailAccountsConfig.from_yaml(yaml_file)
