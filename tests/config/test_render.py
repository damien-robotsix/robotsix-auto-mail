"""Unit tests for ``src/robotsix_auto_mail/config/render.py``."""

from __future__ import annotations

import pytest

from robotsix_auto_mail.config import MailAccount, MailConfig
from robotsix_auto_mail.config.render import (
    _render_account_block,
    _yaml_scalar,
    render_accounts_yaml,
)

# ---------------------------------------------------------------------------
# _yaml_scalar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, "true"),
        (False, "false"),
        (42, "42"),
        (0, "0"),
        (-1, "-1"),
        ("", '""'),
        ("hello", '"hello"'),
        ("hello world", '"hello world"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("back\\slash", '"back\\\\slash"'),
        ("line1\nline2", '"line1\\nline2"'),
        ("tab\there", '"tab\\there"'),
        ("true", '"true"'),
        ("false", '"false"'),
        ("123", '"123"'),
        ("null", '"null"'),
    ],
)
def test_yaml_scalar(value: object, expected: str) -> None:
    assert _yaml_scalar(value) == expected


# ---------------------------------------------------------------------------
# _render_account_block
# ---------------------------------------------------------------------------


def _make_cfg(**overrides: object) -> MailConfig:
    """Minimal MailConfig for rendering tests."""
    kwargs: dict[str, object] = {
        "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com",
        "username": "user@example.com",
        "password": "s3cret",
    }
    kwargs.update(overrides)
    return MailConfig(**kwargs)  # type: ignore[arg-type]


def _block_text(
    cfg: MailConfig, account_id: str = "test", label: str | None = None
) -> str:
    """Return the rendered account block as a single string."""
    account = MailAccount(account_id=account_id, config=cfg, label=label)
    return "\n".join(_render_account_block(account, "  "))


class TestRenderAccountBlock:
    """Tests for ``_render_account_block``."""

    # -- minimal / default -------------------------------------------------

    def test_minimal_account(self) -> None:
        """Password auth, no label, all defaults — minimal block."""
        cfg = _make_cfg()
        text = _block_text(cfg)
        assert 'id: "test"' in text
        assert "label:" not in text
        assert 'username: "user@example.com"' in text
        assert 'password: "s3cret"' in text
        assert "oauth2_provider:" not in text
        assert "ingest:" not in text
        assert "archive:" not in text
        assert "triage:" not in text
        assert "logging:" not in text

    def test_with_label(self) -> None:
        cfg = _make_cfg()
        text = _block_text(cfg, label="Personal Mail")
        assert 'label: "Personal Mail"' in text

    # -- OAuth2 ------------------------------------------------------------

    def test_oauth2_provider_emits_oauth2_fields(self) -> None:
        cfg = _make_cfg(oauth2_provider="microsoft", oauth2_tenant="consumers")
        text = _block_text(cfg)
        assert 'oauth2_provider: "microsoft"' in text
        assert 'oauth2_tenant: "consumers"' in text
        # Password must NOT be present when oauth2_provider is set.
        assert "password:" not in text

    def test_oauth2_token_client_id_secret(self) -> None:
        cfg = _make_cfg(
            oauth2_token="tok",
            oauth2_client_id="cid",
            oauth2_client_secret="csecret",
        )
        text = _block_text(cfg)
        assert 'oauth2_token: "tok"' in text
        assert 'oauth2_client_id: "cid"' in text
        assert 'oauth2_client_secret: "csecret"' in text

    # -- ingest ------------------------------------------------------------

    def test_ingest_non_default(self) -> None:
        cfg = _make_cfg(ingest_interval_minutes=5)
        text = _block_text(cfg)
        assert "ingest:" in text
        assert "interval_minutes: 5" in text

    def test_ingest_default_omitted(self) -> None:
        cfg = _make_cfg()
        text = _block_text(cfg)
        assert "ingest:" not in text

    # -- archive -----------------------------------------------------------

    def test_archive_non_default(self) -> None:
        cfg = _make_cfg(archive_root="/var/mail/archive", archive_enabled=False)
        text = _block_text(cfg)
        assert "archive:" in text
        assert 'root: "/var/mail/archive"' in text
        assert "enabled: false" in text

    def test_archive_default_omitted(self) -> None:
        cfg = _make_cfg()
        text = _block_text(cfg)
        assert "archive:" not in text

    # -- triage ------------------------------------------------------------

    def test_triage_non_default(self) -> None:
        cfg = _make_cfg(triage_on_ingest=False)
        text = _block_text(cfg)
        assert "triage:" in text
        assert "on_ingest: false" in text

    def test_triage_default_omitted(self) -> None:
        cfg = _make_cfg(triage_on_ingest=True)  # True is the default
        text = _block_text(cfg)
        assert "triage:" not in text

    # -- logging -----------------------------------------------------------

    def test_logging_not_emitted_per_account(self) -> None:
        """logging: is application-wide; never emitted per-account."""
        cfg = _make_cfg(log_level="DEBUG", log_format="json")
        text = _block_text(cfg)
        assert "logging:" not in text

    def test_logging_default_omitted(self) -> None:
        cfg = _make_cfg()
        text = _block_text(cfg)
        assert "logging:" not in text

    # -- store / imap / smtp always present --------------------------------

    def test_mandatory_sections_always_present(self) -> None:
        cfg = _make_cfg()
        text = _block_text(cfg)
        assert "imap:" in text
        assert "smtp:" in text
        assert "auth:" in text
        assert "store:" in text
        assert 'folder: "INBOX"' in text
        assert "port: 993" in text
        assert "port: 587" in text


# ---------------------------------------------------------------------------
# render_accounts_yaml
# ---------------------------------------------------------------------------


def _make_account(account_id: str = "default", **overrides: object) -> MailAccount:
    return MailAccount(account_id=account_id, config=_make_cfg(**overrides))


class TestRenderAccountsYaml:
    """Tests for ``render_accounts_yaml``."""

    def test_single_account_structure(self) -> None:
        account = _make_account("main")
        yaml_text = render_accounts_yaml([account], "main")
        assert 'default_account: "main"' in yaml_text
        assert "accounts:" in yaml_text
        assert 'id: "main"' in yaml_text

    def test_multiple_accounts(self) -> None:
        a1 = _make_account("personal")
        a2 = _make_account("work", imap_host="imap.work.com")
        yaml_text = render_accounts_yaml([a1, a2], "personal")
        assert 'id: "personal"' in yaml_text
        assert 'id: "work"' in yaml_text
        assert 'host: "imap.work.com"' in yaml_text

    def test_banner(self) -> None:
        account = _make_account("main")
        yaml_text = render_accounts_yaml(
            [account], "main", banner="# Generated by detect"
        )
        assert yaml_text.startswith("# Generated by detect\n")

    def test_no_banner(self) -> None:
        account = _make_account("main")
        yaml_text = render_accounts_yaml([account], "main")
        # Should start with llm/langfuse or default_account or accounts
        # — no comment prefix.
        assert not yaml_text.startswith("#")

    def test_llm_section_custom_provider_model(self) -> None:
        account = _make_account("main", llm_provider_model="openai-gpt-4o")
        yaml_text = render_accounts_yaml([account], "main")
        assert "llm:" in yaml_text
        assert 'provider_model: "openai-gpt-4o"' in yaml_text

    def test_llm_section_default_omitted(self) -> None:
        # default provider_model is "openrouter-deepseek" and no api_key
        account = _make_account("main")
        yaml_text = render_accounts_yaml([account], "main")
        assert "llm:" not in yaml_text

    def test_llm_section_with_api_key(self) -> None:
        account = _make_account("main", llm_api_key="sk-test")
        yaml_text = render_accounts_yaml([account], "main")
        assert "llm:" in yaml_text
        assert 'api_key: "sk-test"' in yaml_text

    def test_langfuse_section(self) -> None:
        account = _make_account(
            "main",
            langfuse_public_key="pk",
            langfuse_secret_key="sk",
            langfuse_base_url="https://langfuse.example.com",
        )
        yaml_text = render_accounts_yaml([account], "main")
        assert "langfuse:" in yaml_text
        assert 'public_key: "pk"' in yaml_text
        assert 'secret_key: "sk"' in yaml_text
        assert 'base_url: "https://langfuse.example.com"' in yaml_text

    def test_langfuse_section_default_omitted(self) -> None:
        account = _make_account("main")
        yaml_text = render_accounts_yaml([account], "main")
        assert "langfuse:" not in yaml_text

    def test_trailing_newline(self) -> None:
        account = _make_account("main")
        yaml_text = render_accounts_yaml([account], "main")
        assert yaml_text.endswith("\n")
