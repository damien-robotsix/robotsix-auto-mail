"""Tests for ingest interval configuration (ingest_interval_minutes)."""

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


def test_ingest_interval_default() -> None:
    """ingest_interval_minutes defaults to 15."""
    cfg = MailConfig(imap_host="i", smtp_host="s", username="u", password="p")
    assert cfg.ingest_interval_minutes == 15


def test_ingest_interval_default_when_unset() -> None:
    """An account without an explicit interval keeps the default."""
    accts = _accounts()
    assert accts.default.config.ingest_interval_minutes == 15


def test_ingest_interval_custom() -> None:
    """ingest_interval_minutes can be set explicitly."""
    accts = _accounts(ingest_interval_minutes=5)
    assert accts.default.config.ingest_interval_minutes == 5
