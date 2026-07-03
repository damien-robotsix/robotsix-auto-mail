"""Tests for triage-on-ingest configuration setting."""

from __future__ import annotations

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig


def _account(**overrides: object) -> MailAccount:
    base: dict[str, object] = {
        "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com",
        "username": "u",
        "password": "p",
    }
    base.update(overrides)
    return MailAccount(
        account_id="default",
        config=MailConfig(**base),  # type: ignore[arg-type]
    )


def _accounts(**overrides: object) -> MailAccountsConfig:
    return MailAccountsConfig(
        accounts=[_account(**overrides)],
        default_account_id="default",
    )


def test_triage_on_ingest_default() -> None:
    """triage_on_ingest falls back to True when nothing overrides it."""
    cfg = MailConfig(imap_host="i", smtp_host="s", username="u", password="p")
    assert cfg.triage_on_ingest is True


def test_triage_on_ingest_default_when_unset() -> None:
    """An account without an explicit triage_on_ingest keeps it True."""
    accts = _accounts()
    assert accts.default.config.triage_on_ingest is True


def test_triage_on_ingest_custom() -> None:
    """triage_on_ingest can be set to False explicitly."""
    accts = _accounts(triage_on_ingest=False)
    assert accts.default.config.triage_on_ingest is False
