"""Tests for the mail configuration subsystem."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.config import (
    ConfigurationError,
    MailConfig,
    Secrets,
    get_secrets,
    load,
    load_secrets,
)

# ---------------------------------------------------------------------------
# MailConfig basics
# ---------------------------------------------------------------------------


def test_mailconfig_construction_defaults() -> None:
    """All required fields supplied; defaults kick in for optional fields."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
    )
    assert cfg.imap_port == 993
    assert cfg.imap_tls_mode == "direct-tls"
    assert cfg.smtp_port == 587
    assert cfg.smtp_tls_mode == "starttls"
    assert cfg.imap_folder == "INBOX"


def test_mailconfig_imap_folder_explicit() -> None:
    """imap_folder can be set explicitly."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="u",
        password="p",
        imap_folder="Archive",
    )
    assert cfg.imap_folder == "Archive"


def test_mailconfig_is_immutable() -> None:
    """MailConfig is frozen – no attribute assignment after creation."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="u",
        password="p",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.imap_host = "other"  # type: ignore[misc]


def test_mailconfig_repr_redacts_password() -> None:
    """repr() must NOT include the password value."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="u",
        password="s3cret",
    )
    r = repr(cfg)
    assert "s3cret" not in r
    assert "<redacted>" in r


def test_mailconfig_str_redacts_password() -> None:
    """str() must NOT include the password value."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="u",
        password="s3cret",
    )
    s = str(cfg)
    assert "s3cret" not in s
    assert "<redacted>" in s


# ---------------------------------------------------------------------------
# from_env
# ---------------------------------------------------------------------------


def test_from_env_all_required_present() -> None:
    """All required env vars set → valid config."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "user@example.com",
        "MAIL_PASSWORD": "s3cret",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = MailConfig.from_env()
        assert cfg.imap_host == "imap.example.com"
        assert cfg.smtp_host == "smtp.example.com"
        assert cfg.username == "user@example.com"
        assert cfg.password == "s3cret"


def test_from_env_defaults_used_when_absent() -> None:
    """Optional env vars missing → defaults are used."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = MailConfig.from_env()
        assert cfg.imap_port == 993
        assert cfg.imap_tls_mode == "direct-tls"
        assert cfg.smtp_port == 587
        assert cfg.smtp_tls_mode == "starttls"
        assert cfg.imap_folder == "INBOX"


def test_from_env_optional_fields_applied() -> None:
    """All env vars, including optional, are read correctly."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_IMAP_PORT": "143",
        "MAIL_IMAP_TLS_MODE": "starttls",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_SMTP_PORT": "465",
        "MAIL_SMTP_TLS_MODE": "direct-tls",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
        "MAIL_IMAP_FOLDER": "Archive",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = MailConfig.from_env()
        assert cfg.imap_port == 143
        assert cfg.imap_tls_mode == "starttls"
        assert cfg.smtp_port == 465
        assert cfg.smtp_tls_mode == "direct-tls"
        assert cfg.imap_folder == "Archive"


def test_from_env_missing_required_multiple() -> None:
    """Missing multiple required vars → error lists all of them."""
    env: dict[str, str] = {
        "MAIL_SMTP_HOST": "smtp.example.com",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            MailConfig.from_env()
        msg = str(exc.value)
        assert "MAIL_IMAP_HOST" in msg
        assert "MAIL_SMTP_HOST" not in msg  # this one IS set
        assert "MAIL_USERNAME" in msg
        assert "MAIL_PASSWORD" in msg


def test_from_env_missing_all_required() -> None:
    """No env vars at all → error lists every required var."""
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            MailConfig.from_env()
        msg = str(exc.value)
        for key in (
            "MAIL_IMAP_HOST",
            "MAIL_SMTP_HOST",
            "MAIL_USERNAME",
            "MAIL_PASSWORD",
        ):
            assert key in msg


def test_from_env_invalid_port() -> None:
    """Non-integer port → ConfigurationError."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_IMAP_PORT": "not-a-number",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            MailConfig.from_env()
        msg = str(exc.value)
        assert "MAIL_IMAP_PORT" in msg
        assert "not-a-number" in msg


def test_from_env_invalid_tls_mode() -> None:
    """Invalid TLS mode → ConfigurationError."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_IMAP_TLS_MODE": "tls-1.3",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            MailConfig.from_env()
        msg = str(exc.value)
        assert "MAIL_IMAP_TLS_MODE" in msg
        assert "tls-1.3" in msg


def test_from_env_invalid_smtp_tls_mode() -> None:
    """Invalid SMTP TLS mode → ConfigurationError."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_SMTP_TLS_MODE": "nonexistent",
        "MAIL_USERNAME": "u",
        "MAIL_PASSWORD": "p",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            MailConfig.from_env()
        msg = str(exc.value)
        assert "MAIL_SMTP_TLS_MODE" in msg


# ---------------------------------------------------------------------------
# from_toml
# ---------------------------------------------------------------------------


def test_from_toml_example_file() -> None:
    """The bundled example TOML file is valid and parses correctly."""
    cfg = MailConfig.from_toml("config/mail.example.toml")
    assert cfg.imap_host == "imap.example.com"
    assert cfg.imap_port == 993
    assert cfg.imap_tls_mode == "direct-tls"
    assert cfg.smtp_host == "smtp.example.com"
    assert cfg.smtp_port == 587
    assert cfg.smtp_tls_mode == "starttls"
    assert cfg.username == "user@example.com"
    assert cfg.password == "s3cret"
    assert cfg.imap_folder == "INBOX"


def test_from_toml_defaults_for_missing_fields(tmp_path: Path) -> None:
    """Fields missing from TOML fall back to defaults."""
    toml_file = tmp_path / "minimal.toml"
    toml_file.write_text(
        """\
[imap]
host = "imap.example.com"

[smtp]
host = "smtp.example.com"

[auth]
username = "u"
password = "p"
"""
    )
    cfg = MailConfig.from_toml(toml_file)
    assert cfg.imap_port == 993
    assert cfg.imap_tls_mode == "direct-tls"
    assert cfg.smtp_port == 587
    assert cfg.smtp_tls_mode == "starttls"
    assert cfg.imap_folder == "INBOX"


def test_from_toml_custom_imap_folder(tmp_path: Path) -> None:
    """imap_folder can be set via TOML [imap] folder key."""
    toml_file = tmp_path / "folder.toml"
    toml_file.write_text(
        """\
[imap]
host = "imap.example.com"
folder = "Archive"

[smtp]
host = "smtp.example.com"

[auth]
username = "u"
password = "p"
"""
    )
    cfg = MailConfig.from_toml(toml_file)
    assert cfg.imap_folder == "Archive"


def test_from_toml_missing_required_fields(tmp_path: Path) -> None:
    """Missing required TOML fields → ConfigurationError with all names."""
    toml_file = tmp_path / "bad.toml"
    toml_file.write_text(
        """\
[imap]
port = 993

[smtp]
tls_mode = "none"
"""
    )
    with pytest.raises(ConfigurationError) as exc:
        MailConfig.from_toml(toml_file)
    msg = str(exc.value)
    assert "imap.host" in msg
    assert "smtp.host" in msg
    assert "auth.username" in msg
    # auth.password is no longer required — it can come from secrets.yaml


def test_from_toml_invalid_tls_mode(tmp_path: Path) -> None:
    """Invalid TLS mode in TOML → ConfigurationError."""
    toml_file = tmp_path / "bad_tls.toml"
    toml_file.write_text(
        """\
[imap]
host = "imap.example.com"
tls_mode = "bad-mode"

[smtp]
host = "smtp.example.com"

[auth]
username = "u"
password = "p"
"""
    )
    with pytest.raises(ConfigurationError) as exc:
        MailConfig.from_toml(toml_file)
    msg = str(exc.value)
    assert "imap.tls_mode" in msg
    assert "bad-mode" in msg


def test_from_toml_malformed_file(tmp_path: Path) -> None:
    """Malformed TOML → ConfigurationError."""
    toml_file = tmp_path / "malformed.toml"
    toml_file.write_text("this is not valid TOML {{{")
    with pytest.raises(ConfigurationError) as exc:
        MailConfig.from_toml(toml_file)
    assert "Invalid TOML" in str(exc.value)


def test_from_toml_file_not_found(tmp_path: Path) -> None:
    """Missing file → FileNotFoundError (not swallowed)."""
    missing = tmp_path / "does_not_exist.toml"
    with pytest.raises(FileNotFoundError):
        MailConfig.from_toml(missing)


def test_from_toml_wrong_type_for_field(tmp_path: Path) -> None:
    """Field with wrong type (e.g. port as string) → ConfigurationError."""
    toml_file = tmp_path / "bad_port.toml"
    toml_file.write_text(
        """\
[imap]
host = "imap.example.com"
port = "not-a-number"

[smtp]
host = "smtp.example.com"

[auth]
username = "u"
password = "p"
"""
    )
    with pytest.raises(ConfigurationError) as exc:
        MailConfig.from_toml(toml_file)
    assert "port" in str(exc.value)


# ---------------------------------------------------------------------------
# from_yaml
# ---------------------------------------------------------------------------


def test_from_yaml_example_file() -> None:
    """The bundled example YAML file is valid and parses correctly."""
    cfg = MailConfig.from_yaml("config/mail.local.example.yaml")
    assert cfg.imap_host == "imap.example.com"
    assert cfg.imap_port == 993
    assert cfg.imap_tls_mode == "direct-tls"
    assert cfg.smtp_host == "smtp.example.com"
    assert cfg.smtp_port == 587
    assert cfg.smtp_tls_mode == "starttls"
    assert cfg.username == "user@example.com"
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
    # auth.password is no longer required — it can come from secrets.yaml


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


# ---------------------------------------------------------------------------
# load() convenience function
# ---------------------------------------------------------------------------


def test_load_env_only() -> None:
    """load() with all env vars set returns env config (no TOML needed)."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.env.com",
        "MAIL_SMTP_HOST": "smtp.env.com",
        "MAIL_USERNAME": "env_user",
        "MAIL_PASSWORD": "env_pass",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = load()
        assert cfg.imap_host == "imap.env.com"
        assert cfg.smtp_host == "smtp.env.com"
        assert cfg.username == "env_user"
        assert cfg.password == "env_pass"


def test_load_fallback_to_toml(tmp_path: Path) -> None:
    """No env vars → load() falls back to TOML at given path."""
    toml_file = tmp_path / "test.toml"
    toml_file.write_text(
        """\
[imap]
host = "imap.toml.com"

[smtp]
host = "smtp.toml.com"

[auth]
username = "toml_user"
password = "toml_pass"
"""
    )
    env: dict[str, str] = {"MAIL_CONFIG_PATH": str(toml_file)}
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = load()
        assert cfg.imap_host == "imap.toml.com"
        assert cfg.smtp_host == "smtp.toml.com"
        assert cfg.username == "toml_user"
        assert cfg.password == "toml_pass"


def test_load_env_overrides_toml(tmp_path: Path) -> None:
    """Single env var overrides the corresponding TOML field."""
    toml_file = tmp_path / "test.toml"
    toml_file.write_text(
        """\
[imap]
host = "imap.toml.com"

[smtp]
host = "smtp.toml.com"

[auth]
username = "toml_user"
password = "toml_pass"
"""
    )
    env: dict[str, str] = {
        "MAIL_CONFIG_PATH": str(toml_file),
        "MAIL_IMAP_HOST": "imap.env.com",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = load()
        # env wins for IMAP host
        assert cfg.imap_host == "imap.env.com"
        # SMTP still from TOML
        assert cfg.smtp_host == "smtp.toml.com"
        assert cfg.username == "toml_user"


def test_load_env_overrides_toml_folder(tmp_path: Path) -> None:
    """MAIL_IMAP_FOLDER env var overrides TOML folder."""
    toml_file = tmp_path / "test.toml"
    toml_file.write_text(
        """\
[imap]
host = "imap.toml.com"
folder = "INBOX"

[smtp]
host = "smtp.toml.com"

[auth]
username = "toml_user"
password = "toml_pass"
"""
    )
    env: dict[str, str] = {
        "MAIL_CONFIG_PATH": str(toml_file),
        "MAIL_IMAP_FOLDER": "Archive",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = load()
        assert cfg.imap_folder == "Archive"


def test_load_missing_config_file() -> None:
    """No env vars AND no config file → ConfigurationError."""
    env: dict[str, str] = {
        "MAIL_CONFIG_PATH": "/nonexistent/path/mail.toml",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError):
            load()


def test_load_yaml_with_defaults(tmp_path: Path) -> None:
    """load() deep-merges YAML defaults + local overrides + env reapply."""
    defaults_file = tmp_path / "mail.defaults.yaml"
    defaults_file.write_text(
        """\
imap:
  host: ""
  port: 993
  tls_mode: direct-tls
  folder: INBOX

smtp:
  host: ""
  port: 587
  tls_mode: starttls

auth:
  username: ""
  password: ""

store:
  path: /default/path/mail.db
"""
    )

    local_file = tmp_path / "mail.local.yaml"
    local_file.write_text(
        """\
imap:
  host: imap.overrides.com

smtp:
  host: smtp.from.local.com

auth:
  username: override_user
  password: override_pass
"""
    )

    env: dict[str, str] = {
        "MAIL_CONFIG_PATH": str(local_file),
        "MAIL_DEFAULTS_PATH": str(defaults_file),
        "MAIL_SMTP_HOST": "smtp.from.env.com",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = load()

    # YAML local overrides the defaults for imap.host.
    assert cfg.imap_host == "imap.overrides.com"
    # SMTP from local, overridden by env.
    assert cfg.smtp_host == "smtp.from.env.com"  # env overrides local
    # Auth from local.
    assert cfg.username == "override_user"
    assert cfg.password == "override_pass"
    # port / tls_mode from defaults (not in local, not in env).
    assert cfg.imap_port == 993
    assert cfg.imap_tls_mode == "direct-tls"
    assert cfg.smtp_port == 587
    assert cfg.smtp_tls_mode == "starttls"
    # db_path from defaults.
    assert cfg.db_path == "/default/path/mail.db"
    # imap_folder from defaults.
    assert cfg.imap_folder == "INBOX"


# ---------------------------------------------------------------------------
# ConfigurationError
# ---------------------------------------------------------------------------


def test_load_re_raises_on_invalid_value_not_missing(tmp_path: Path) -> None:
    """load() must NOT fall back to TOML when env has an invalid value.

    If from_env() fails because of an invalid value (e.g. a non-integer
    port), the user explicitly set the env var — falling back to TOML
    would silently swallow their typo.
    """
    toml_file = tmp_path / "test.toml"
    toml_file.write_text(
        """\
[imap]
host = "imap.toml.com"

[smtp]
host = "smtp.toml.com"

[auth]
username = "toml_user"
password = "toml_pass"
"""
    )
    env: dict[str, str] = {
        "MAIL_CONFIG_PATH": str(toml_file),
        "MAIL_IMAP_HOST": "imap.env.com",
        "MAIL_SMTP_HOST": "smtp.env.com",
        "MAIL_USERNAME": "env_user",
        "MAIL_PASSWORD": "env_pass",
        "MAIL_IMAP_PORT": "not-a-number",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            load()
        msg = str(exc.value)
        assert "MAIL_IMAP_PORT" in msg
        assert "not-a-number" in msg


def test_load_re_raises_on_invalid_tls_not_missing(tmp_path: Path) -> None:
    """load() must re-raise when TLS mode is invalid, even if all
    required fields are present."""
    toml_file = tmp_path / "test.toml"
    toml_file.write_text(
        """\
[imap]
host = "imap.toml.com"

[smtp]
host = "smtp.toml.com"

[auth]
username = "toml_user"
password = "toml_pass"
"""
    )
    env: dict[str, str] = {
        "MAIL_CONFIG_PATH": str(toml_file),
        "MAIL_IMAP_HOST": "imap.env.com",
        "MAIL_SMTP_HOST": "smtp.env.com",
        "MAIL_USERNAME": "env_user",
        "MAIL_PASSWORD": "env_pass",
        "MAIL_IMAP_TLS_MODE": "tls-9.9",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            load()
        msg = str(exc.value)
        assert "MAIL_IMAP_TLS_MODE" in msg
        assert "tls-9.9" in msg


def test_configuration_error_is_exception() -> None:
    """ConfigurationError is a proper Exception subclass."""
    err = ConfigurationError("test message")
    assert isinstance(err, Exception)
    assert str(err) == "test message"
    assert err.message == "test message"


def test_configuration_error_missing_only_default() -> None:
    """missing_only defaults to False."""
    err = ConfigurationError("test")
    assert err.missing_only is False


def test_configuration_error_missing_only_true() -> None:
    """missing_only can be set to True."""
    err = ConfigurationError("test", missing_only=True)
    assert err.missing_only is True


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


def test_secrets_repr_redaction_with_password() -> None:
    """repr(Secrets) redacts the password value."""
    s = Secrets(mail_password="s3cret")
    r = repr(s)
    assert "s3cret" not in r
    assert "<redacted>" in r


def test_secrets_repr_redaction_default() -> None:
    """Default Secrets() repr still shows <redacted>."""
    s = Secrets()
    r = repr(s)
    assert "<redacted>" in r


def test_secrets_str_redaction() -> None:
    """str(Secrets) does NOT contain the actual password."""
    s = Secrets(mail_password="s3cret")
    r = str(s)
    assert "s3cret" not in r


# -- load_secrets ----------------------------------------------------------


def test_load_secrets_happy_path(tmp_path: Path) -> None:
    """load_secrets reads mail_password from a valid YAML file."""
    secrets_file = tmp_path / "secrets.yaml"
    secrets_file.write_text("mail_password: my-pass\n")
    result = load_secrets(secrets_file)
    assert result.mail_password == "my-pass"


def test_load_secrets_missing_file(tmp_path: Path) -> None:
    """load_secrets returns empty Secrets when file does not exist."""
    missing = tmp_path / "nonexistent.yaml"
    result = load_secrets(missing)
    assert result.mail_password == ""
    assert isinstance(result, Secrets)


def test_load_secrets_empty_file(tmp_path: Path) -> None:
    """load_secrets returns empty Secrets for an empty YAML file."""
    secrets_file = tmp_path / "empty.yaml"
    secrets_file.write_text("")
    result = load_secrets(secrets_file)
    assert result.mail_password == ""


def test_load_secrets_missing_key(tmp_path: Path) -> None:
    """load_secrets returns empty password when mail_password key is absent."""
    secrets_file = tmp_path / "other.yaml"
    secrets_file.write_text("other: value\n")
    result = load_secrets(secrets_file)
    assert result.mail_password == ""


def test_load_secrets_bad_yaml(tmp_path: Path) -> None:
    """load_secrets raises ConfigurationError for malformed YAML."""
    secrets_file = tmp_path / "bad.yaml"
    secrets_file.write_text("{this is: not: valid: yaml")
    with pytest.raises(ConfigurationError) as exc:
        load_secrets(secrets_file)
    assert "Invalid YAML" in str(exc.value)


# -- get_secrets caching ---------------------------------------------------


def test_get_secrets_caching() -> None:
    """get_secrets() caches the result and only calls load_secrets once."""
    import robotsix_auto_mail.config as config_mod

    # Reset cache before test
    config_mod._secrets_cache = None

    with mock.patch(
        "robotsix_auto_mail.config.load_secrets",
        return_value=Secrets(mail_password="first"),
    ) as mock_load:
        s1 = get_secrets()
        s2 = get_secrets()
        assert s1 is s2
        assert mock_load.call_count == 1

    # Clean up
    config_mod._secrets_cache = None


# -- load() with secrets ---------------------------------------------------


def test_load_with_secrets(tmp_path: Path) -> None:
    """load() applies secrets.mail_password on top of file config."""
    import robotsix_auto_mail.config as config_mod

    # Reset cache
    config_mod._secrets_cache = None

    local_yaml = tmp_path / "mail.local.yaml"
    local_yaml.write_text(
        """\
imap:
  host: imap.example.com

smtp:
  host: smtp.example.com

auth:
  username: user@example.com
  password: ""
"""
    )

    secrets_yaml = tmp_path / "secrets.yaml"
    secrets_yaml.write_text("mail_password: secret-pass\n")

    env: dict[str, str] = {
        "MAIL_CONFIG_PATH": str(local_yaml),
        "MAIL_SECRETS_FILE": str(secrets_yaml),
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = load()
        assert cfg.password == "secret-pass"
        assert cfg.imap_host == "imap.example.com"
        assert cfg.smtp_host == "smtp.example.com"
        assert cfg.username == "user@example.com"

    # Clean up
    config_mod._secrets_cache = None


def test_load_without_secrets_file(tmp_path: Path) -> None:
    """load() preserves file password when no secrets file exists."""
    import robotsix_auto_mail.config as config_mod

    # Reset cache
    config_mod._secrets_cache = None

    local_yaml = tmp_path / "mail.local.yaml"
    local_yaml.write_text(
        """\
imap:
  host: imap.example.com

smtp:
  host: smtp.example.com

auth:
  username: user@example.com
  password: "file-pass"
"""
    )

    env: dict[str, str] = {
        "MAIL_CONFIG_PATH": str(local_yaml),
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = load()
        assert cfg.password == "file-pass"

    # Clean up
    config_mod._secrets_cache = None


# -- from_yaml / from_toml: password not required -------------------------


def test_from_yaml_missing_auth_password_ok(tmp_path: Path) -> None:
    """from_yaml with validate=True does NOT require auth.password."""
    yaml_file = tmp_path / "no_pass.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.example.com

smtp:
  host: smtp.example.com

auth:
  username: user@example.com
"""
    )
    cfg = MailConfig.from_yaml(yaml_file, validate=True)
    assert cfg.password == ""
    assert cfg.username == "user@example.com"


def test_from_toml_missing_auth_password_ok(tmp_path: Path) -> None:
    """from_toml does NOT require auth.password."""
    toml_file = tmp_path / "no_pass.toml"
    toml_file.write_text(
        """\
[imap]
host = "imap.example.com"

[smtp]
host = "smtp.example.com"

[auth]
username = "user@example.com"
"""
    )
    cfg = MailConfig.from_toml(toml_file)
    assert cfg.password == ""


# -- from_env still requires MAIL_PASSWORD --------------------------------


def test_from_env_still_requires_mail_password() -> None:
    """from_env raises ConfigurationError when MAIL_PASSWORD is missing."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.example.com",
        "MAIL_SMTP_HOST": "smtp.example.com",
        "MAIL_USERNAME": "user@example.com",
        # MAIL_PASSWORD intentionally missing
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            MailConfig.from_env()
        msg = str(exc.value)
        assert "MAIL_PASSWORD" in msg
