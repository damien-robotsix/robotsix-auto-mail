"""Tests for MailConfig basics — construction, immutability, repr/str redaction."""

from __future__ import annotations

import pydantic
import pytest

from robotsix_auto_mail.config import MailConfig

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
    """MailConfig is frozen - no attribute assignment after creation."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="u",
        password="p",
    )
    with pytest.raises(pydantic.ValidationError):
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


def test_mailconfig_langfuse_defaults_empty() -> None:
    """Langfuse fields default to empty strings when unset."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="u",
        password="p",
    )
    assert cfg.langfuse_public_key == ""
    assert cfg.langfuse_secret_key.get_secret_value() == ""
    assert cfg.langfuse_base_url == ""


def test_mailconfig_repr_redacts_langfuse_secret_key() -> None:
    """repr() must NOT include the langfuse_secret_key value."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="u",
        password="p",
        langfuse_secret_key="sk-lf-supersecret",
    )
    r = repr(cfg)
    assert "sk-lf-supersecret" not in r
    assert "<redacted>" in r
