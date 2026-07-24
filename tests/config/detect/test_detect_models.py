"""Tests for detect model validation, dataclass construction, and conversion."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pydantic
import pytest

from robotsix_auto_mail.config.detect import (
    DetectedProvider,
    MailProvider,
    is_microsoft_provider,
    provider_to_config,
)


# ---------------------------------------------------------------------------
# DetectedProvider — validation
# ---------------------------------------------------------------------------


def test_detected_provider_valid_construction() -> None:
    """A DetectedProvider with both required hosts constructs fine."""
    dp = DetectedProvider(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
    )
    assert dp.imap_host == "imap.example.com"
    assert dp.smtp_host == "smtp.example.com"


def test_detected_provider_defaults() -> None:
    """Fields not supplied fall back to their declared defaults."""
    dp = DetectedProvider(imap_host="imap.example.com", smtp_host="smtp.example.com")
    assert dp.imap_port == 993
    assert dp.imap_tls_mode == "direct-tls"
    assert dp.smtp_port == 587
    assert dp.smtp_tls_mode == "starttls"


def test_detected_provider_missing_imap_host() -> None:
    """Missing imap_host raises pydantic.ValidationError."""
    with pytest.raises(pydantic.ValidationError):
        DetectedProvider(smtp_host="smtp.example.com")  # type: ignore[call-arg]


def test_detected_provider_missing_smtp_host() -> None:
    """Missing smtp_host raises pydantic.ValidationError."""
    with pytest.raises(pydantic.ValidationError):
        DetectedProvider(imap_host="imap.example.com")  # type: ignore[call-arg]


def test_detected_provider_imap_port_zero() -> None:
    """imap_port=0 violates ge=1."""
    with pytest.raises(pydantic.ValidationError):
        DetectedProvider(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            imap_port=0,
        )


def test_detected_provider_imap_port_negative() -> None:
    """imap_port=-1 violates ge=1."""
    with pytest.raises(pydantic.ValidationError):
        DetectedProvider(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            imap_port=-1,
        )


def test_detected_provider_imap_port_over_max() -> None:
    """imap_port=65536 violates le=65535."""
    with pytest.raises(pydantic.ValidationError):
        DetectedProvider(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            imap_port=65536,
        )


def test_detected_provider_invalid_imap_tls_mode() -> None:
    """An invalid imap_tls_mode string raises pydantic.ValidationError."""
    with pytest.raises(pydantic.ValidationError) as exc:
        DetectedProvider(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            imap_tls_mode="bad",
        )
    assert "imap_tls_mode" in str(exc.value)
    assert "bad" in str(exc.value)


def test_detected_provider_invalid_smtp_tls_mode() -> None:
    """An invalid smtp_tls_mode string raises pydantic.ValidationError."""
    with pytest.raises(pydantic.ValidationError) as exc:
        DetectedProvider(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            smtp_tls_mode="bad",
        )
    assert "smtp_tls_mode" in str(exc.value)
    assert "bad" in str(exc.value)


def test_detected_provider_accepts_valid_tls_modes() -> None:
    """All three valid TLS modes are accepted for both fields."""
    for mode in ("starttls", "direct-tls", "none"):
        dp = DetectedProvider(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            imap_tls_mode=mode,
            smtp_tls_mode=mode,
        )
        assert dp.imap_tls_mode == mode
        assert dp.smtp_tls_mode == mode


# ---------------------------------------------------------------------------
# MailProvider
# ---------------------------------------------------------------------------


def test_mail_provider_construction_all_fields() -> None:
    """MailProvider can be constructed with every field explicit."""
    mp = MailProvider(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        imap_port=143,
        imap_tls_mode="starttls",
        smtp_port=465,
        smtp_tls_mode="direct-tls",
    )
    assert mp.imap_host == "imap.example.com"
    assert mp.smtp_host == "smtp.example.com"
    assert mp.imap_port == 143
    assert mp.imap_tls_mode == "starttls"
    assert mp.smtp_port == 465
    assert mp.smtp_tls_mode == "direct-tls"


def test_mail_provider_default_values() -> None:
    """MailProvider ports and TLS modes have expected defaults."""
    mp = MailProvider(imap_host="ih", smtp_host="sh")
    assert mp.imap_port == 993
    assert mp.imap_tls_mode == "direct-tls"
    assert mp.smtp_port == 587
    assert mp.smtp_tls_mode == "starttls"


def test_mail_provider_is_immutable() -> None:
    """MailProvider is frozen — no attribute assignment after creation."""
    mp = MailProvider(imap_host="ih", smtp_host="sh")
    with pytest.raises(FrozenInstanceError):
        mp.imap_host = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# provider_to_config
# ---------------------------------------------------------------------------


def test_provider_to_config_maps_correctly() -> None:
    """provider_to_config maps all MailProvider fields to MailConfig."""
    mp = MailProvider(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        imap_port=143,
        imap_tls_mode="starttls",
        smtp_port=465,
        smtp_tls_mode="direct-tls",
    )
    cfg = provider_to_config(mp, "user@example.com")
    assert cfg.imap_host == "imap.example.com"
    assert cfg.imap_port == 143
    assert cfg.imap_tls_mode == "starttls"
    assert cfg.smtp_host == "smtp.example.com"
    assert cfg.smtp_port == 465
    assert cfg.smtp_tls_mode == "direct-tls"
    assert cfg.username == "user@example.com"


def test_provider_to_config_password_defaults_empty() -> None:
    """The password field defaults to the empty string."""
    mp = MailProvider(imap_host="ih", smtp_host="sh")
    cfg = provider_to_config(mp, "user@example.com")
    assert cfg.password.get_secret_value() == ""


def test_provider_to_config_password_forwarded() -> None:
    """An explicit password is forwarded to MailConfig."""
    mp = MailProvider(imap_host="ih", smtp_host="sh")
    cfg = provider_to_config(mp, "user@example.com", password="s3cret")
    assert cfg.password.get_secret_value() == "s3cret"


def test_provider_to_config_imap_folder_defaults_to_inbox() -> None:
    """imap_folder defaults to 'INBOX'."""
    mp = MailProvider(imap_host="ih", smtp_host="sh")
    cfg = provider_to_config(mp, "user@example.com")
    assert cfg.imap_folder == "INBOX"


def test_provider_to_config_default_db_path() -> None:
    """db_path defaults to '' (the accounts loader derives it per account)."""
    mp = MailProvider(imap_host="ih", smtp_host="sh")
    cfg = provider_to_config(mp, "user@example.com")
    assert cfg.db_path == ""


def test_provider_to_config_explicit_db_path() -> None:
    """An explicit db_path is forwarded to MailConfig."""
    mp = MailProvider(imap_host="ih", smtp_host="sh")
    cfg = provider_to_config(mp, "user@example.com", db_path="custom/path.db")
    assert cfg.db_path == "custom/path.db"


# ---------------------------------------------------------------------------
# is_microsoft_provider — host classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("imap_host", "smtp_host"),
    [
        ("outlook.office365.com", "smtp.office365.com"),
        ("OUTLOOK.OFFICE365.COM", "SMTP.OFFICE365.COM"),
        ("eur.olc.protection.office365.com", "smtp.office365.com"),
        ("imap.example.com", "smtp.office365.com"),
        ("outlook.com", "smtp-mail.outlook.com"),
    ],
)
def test_is_microsoft_provider_true(imap_host: str, smtp_host: str) -> None:
    """Microsoft 365 / Outlook.com hosts are recognised, case-insensitively."""
    mp = MailProvider(imap_host=imap_host, smtp_host=smtp_host)
    assert is_microsoft_provider(mp) is True


@pytest.mark.parametrize(
    ("imap_host", "smtp_host"),
    [
        ("imap.gmail.com", "smtp.gmail.com"),
        ("imap.fastmail.com", "smtp.fastmail.com"),
        ("mail.example.com", "mail.example.com"),
    ],
)
def test_is_microsoft_provider_false(imap_host: str, smtp_host: str) -> None:
    """Non-Microsoft providers are not misclassified."""
    mp = MailProvider(imap_host=imap_host, smtp_host=smtp_host)
    assert is_microsoft_provider(mp) is False


def test_provider_to_config_microsoft_writes_oauth2_no_password() -> None:
    """A Microsoft provider yields an OAuth2 config with no password."""
    mp = MailProvider(imap_host="outlook.office365.com", smtp_host="smtp.office365.com")
    cfg = provider_to_config(mp, "user@contoso.com", password="ignored")
    assert cfg.oauth2_provider == "microsoft"
    assert cfg.oauth2_tenant == "organizations"
    assert cfg.oauth2_client_id == ""
    assert cfg.password.get_secret_value() == ""
