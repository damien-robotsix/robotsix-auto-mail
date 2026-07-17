"""Unit tests for configuration model validators and error paths.

Covers ``MailConfig`` field validators, ``_validate_template_literals``,
``MailAccount._validate_account_id``, ``MailAccountsConfig._validate`` /
``.default`` / ``.get()`` / ``.ids()``, and ``MailConfig.__repr__`` /
``__str__`` secret-field masking.
"""

from __future__ import annotations

import pytest

from robotsix_auto_mail.config.model import (
    MailAccount,
    MailAccountsConfig,
    MailConfig,
    _validate_template_literals,
)
from robotsix_auto_mail.config.schema import ConfigurationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: object) -> MailConfig:
    """Return a minimal valid ``MailConfig``, optionally overriding fields."""
    defaults: dict[str, object] = {
        "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com",
        "username": "user@example.com",
        "password": "s3cret",
    }
    defaults.update(overrides)
    return MailConfig(**defaults)  # type: ignore[arg-type]


def _make_account(
    account_id: str = "default",
    **config_overrides: object,
) -> MailAccount:
    return MailAccount(
        account_id=account_id,
        config=_make_config(**config_overrides),
    )


# ---------------------------------------------------------------------------
# _validate_template_literals
# ---------------------------------------------------------------------------


class TestValidateTemplateLiterals:
    """Standalone function — NOT called by pydantic; tested directly."""

    def test_clean_config_passes(self) -> None:
        _validate_template_literals(_make_config())

    def test_imap_host_with_placeholder_raises(self) -> None:
        cfg = _make_config(imap_host="{accounts.0.imap_host}")
        with pytest.raises(ConfigurationError) as exc:
            _validate_template_literals(cfg)
        assert "imap_host" in exc.value.message
        assert "template literal" in exc.value.message

    def test_smtp_host_with_placeholder_raises(self) -> None:
        cfg = _make_config(smtp_host="{accounts.0.smtp_host}")
        with pytest.raises(ConfigurationError) as exc:
            _validate_template_literals(cfg)
        assert "smtp_host" in exc.value.message

    def test_username_with_placeholder_raises(self) -> None:
        cfg = _make_config(username="{accounts.0.username}")
        with pytest.raises(ConfigurationError) as exc:
            _validate_template_literals(cfg)
        assert "username" in exc.value.message

    def test_password_with_placeholder_raises_and_redacts(self) -> None:
        cfg = _make_config(password="{accounts.0.password}")
        with pytest.raises(ConfigurationError) as exc:
            _validate_template_literals(cfg)
        assert "password" in exc.value.message
        assert "<redacted>" in exc.value.message
        assert "{accounts.0.password}" not in exc.value.message

    def test_imap_folder_with_placeholder_raises(self) -> None:
        cfg = _make_config(imap_folder="{accounts.0.imap_folder}")
        with pytest.raises(ConfigurationError) as exc:
            _validate_template_literals(cfg)
        assert "imap_folder" in exc.value.message

    def test_empty_field_is_skipped(self) -> None:
        """Fields that are empty strings are not checked for templates."""
        cfg = _make_config(imap_folder="")
        _validate_template_literals(cfg)  # does not raise


# ---------------------------------------------------------------------------
# MailConfig field validators
# ---------------------------------------------------------------------------


class TestMailConfigTlsValidators:
    def test_imap_tls_mode_invalid_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="imap_tls_mode"):
            _make_config(imap_tls_mode="bogus")

    def test_smtp_tls_mode_invalid_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="smtp_tls_mode"):
            _make_config(smtp_tls_mode="bogus")

    @pytest.mark.parametrize("mode", ["starttls", "direct-tls", "none"])
    def test_imap_tls_mode_valid(self, mode: str) -> None:
        cfg = _make_config(imap_tls_mode=mode)
        assert cfg.imap_tls_mode == mode

    @pytest.mark.parametrize("mode", ["starttls", "direct-tls", "none"])
    def test_smtp_tls_mode_valid(self, mode: str) -> None:
        cfg = _make_config(smtp_tls_mode=mode)
        assert cfg.smtp_tls_mode == mode


class TestMailConfigLogValidators:
    def test_log_level_invalid_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="log_level"):
            _make_config(log_level="TRACE")

    @pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    def test_log_level_valid(self, level: str) -> None:
        cfg = _make_config(log_level=level)
        assert cfg.log_level == level

    @pytest.mark.parametrize("level", ["debug", "info", "warning", "error", "critical"])
    def test_log_level_case_insensitive_upper(self, level: str) -> None:
        cfg = _make_config(log_level=level)
        assert cfg.log_level == level.upper()

    def test_log_format_invalid_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="log_format"):
            _make_config(log_format="xml")

    def test_log_format_valid_json(self) -> None:
        cfg = _make_config(log_format="json")
        assert cfg.log_format == "json"

    def test_log_format_valid_console(self) -> None:
        cfg = _make_config(log_format="console")
        assert cfg.log_format == "console"

    @pytest.mark.parametrize("fmt", ["JSON", "Console"])
    def test_log_format_case_insensitive_lower(self, fmt: str) -> None:
        cfg = _make_config(log_format=fmt)
        assert cfg.log_format == fmt.lower()


# ---------------------------------------------------------------------------
# MailConfig __repr__ / __str__ secret masking
# ---------------------------------------------------------------------------


class TestMailConfigReprMasking:
    def test_password_redacted(self) -> None:
        cfg = _make_config(password="super-secret-pw")
        r = repr(cfg)
        assert "super-secret-pw" not in r
        assert "password=<redacted>" in r

    def test_llm_api_key_redacted(self) -> None:
        cfg = _make_config(llm_api_key="sk-1234567890")
        r = repr(cfg)
        assert "sk-1234567890" not in r
        assert "llm_api_key=<redacted>" in r

    def test_oauth2_token_redacted(self) -> None:
        cfg = _make_config(oauth2_token="ya29.secret-token")
        r = repr(cfg)
        assert "ya29.secret-token" not in r
        assert "oauth2_token=<redacted>" in r

    def test_oauth2_client_secret_redacted(self) -> None:
        cfg = _make_config(oauth2_client_secret="GOCSPX-secret")
        r = repr(cfg)
        assert "GOCSPX-secret" not in r
        assert "oauth2_client_secret=<redacted>" in r

    def test_langfuse_secret_key_redacted(self) -> None:
        cfg = _make_config(langfuse_secret_key="sk-lf-abcdef")
        r = repr(cfg)
        assert "sk-lf-abcdef" not in r
        assert "langfuse_secret_key=<redacted>" in r

    def test_non_secret_fields_visible(self) -> None:
        cfg = _make_config(username="alice@example.com", imap_host="mail.example.com")
        r = repr(cfg)
        assert "alice@example.com" in r
        assert "mail.example.com" in r

    def test_str_delegates_to_repr(self) -> None:
        cfg = _make_config(password="pw")
        s = str(cfg)
        assert "pw" not in s
        assert "password=<redacted>" in s

    def test_empty_secret_fields_stay_redacted(self) -> None:
        """Even empty secret fields should appear as <redacted>."""
        cfg = _make_config()
        r = repr(cfg)
        assert "llm_api_key=<redacted>" in r
        assert "oauth2_token=<redacted>" in r


# ---------------------------------------------------------------------------
# MailAccount._validate_account_id
# ---------------------------------------------------------------------------


class TestMailAccountValidateAccountId:
    def test_empty_string_raises_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError, match="non-empty"):
            _make_account(account_id="")

    def test_space_character_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="must match"):
            _make_account(account_id="my account")

    def test_at_sign_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="must match"):
            _make_account(account_id="user@host")

    def test_slash_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="must match"):
            _make_account(account_id="a/b")

    @pytest.mark.parametrize(
        "valid_id", ["personal", "work-account", "test.account_123", "A", "Z"]
    )
    def test_valid_ids_pass(self, valid_id: str) -> None:
        account = _make_account(account_id=valid_id)
        assert account.account_id == valid_id


# ---------------------------------------------------------------------------
# MailAccountsConfig._validate (model_validator)
# ---------------------------------------------------------------------------


class TestMailAccountsConfigValidate:
    def test_empty_accounts_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="must not be empty"):
            MailAccountsConfig(accounts=[], default_account_id="any")

    def test_duplicate_account_ids_raises(self) -> None:
        a1 = _make_account(account_id="dup")
        a2 = _make_account(account_id="dup")
        with pytest.raises(ConfigurationError, match="duplicate account_id"):
            MailAccountsConfig(accounts=[a1, a2], default_account_id="dup")

    def test_duplicate_db_paths_raises(self) -> None:
        a1 = _make_account(account_id="a", db_path="/same/path.db")
        a2 = _make_account(account_id="b", db_path="/same/path.db")
        with pytest.raises(ConfigurationError, match="duplicate db_path"):
            MailAccountsConfig(accounts=[a1, a2], default_account_id="a")

    def test_empty_db_paths_are_skipped(self) -> None:
        """Accounts with empty db_path are not checked for duplicates."""
        a1 = _make_account(account_id="a", db_path="")
        a2 = _make_account(account_id="b", db_path="")
        cfg = MailAccountsConfig(accounts=[a1, a2], default_account_id="a")
        assert len(cfg.accounts) == 2

    def test_unresolvable_default_account_id_raises(self) -> None:
        a1 = _make_account(account_id="a")
        with pytest.raises(ConfigurationError, match="not in accounts"):
            MailAccountsConfig(accounts=[a1], default_account_id="nonexistent")

    def test_valid_config_passes(self) -> None:
        a1 = _make_account(account_id="a", db_path="/a.db")
        a2 = _make_account(account_id="b", db_path="/b.db")
        cfg = MailAccountsConfig(accounts=[a1, a2], default_account_id="b")
        assert cfg.default_account_id == "b"
        assert len(cfg.accounts) == 2


# ---------------------------------------------------------------------------
# MailAccountsConfig.default / .get / .ids
# ---------------------------------------------------------------------------


class TestMailAccountsConfigAccessors:
    @pytest.fixture
    def accounts_cfg(self) -> MailAccountsConfig:
        a1 = _make_account(account_id="alpha")
        a2 = _make_account(account_id="beta")
        return MailAccountsConfig(accounts=[a1, a2], default_account_id="alpha")

    def test_default_returns_default_account(
        self, accounts_cfg: MailAccountsConfig
    ) -> None:
        default = accounts_cfg.default
        assert default.account_id == "alpha"

    def test_get_valid_id(self, accounts_cfg: MailAccountsConfig) -> None:
        account = accounts_cfg.get("beta")
        assert account.account_id == "beta"

    def test_get_unknown_id_raises_configuration_error(
        self, accounts_cfg: MailAccountsConfig
    ) -> None:
        with pytest.raises(ConfigurationError, match="unknown account_id"):
            accounts_cfg.get("gamma")

    def test_get_unknown_id_lists_valid_ids(
        self, accounts_cfg: MailAccountsConfig
    ) -> None:
        with pytest.raises(ConfigurationError) as exc:
            accounts_cfg.get("gamma")
        assert "alpha" in exc.value.message
        assert "beta" in exc.value.message

    def test_ids_returns_ordered_tuple(self, accounts_cfg: MailAccountsConfig) -> None:
        result = accounts_cfg.ids()
        assert result == ("alpha", "beta")
        assert isinstance(result, tuple)
