"""Tests for archive configuration settings (archive_root, archive_enabled)."""

from __future__ import annotations

import pydantic
import pytest

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


def test_archive_defaults() -> None:
    """archive_root / archive_enabled fall back to their defaults."""
    cfg = MailConfig(imap_host="i", smtp_host="s", username="u", password="p")
    assert cfg.archive_root == "robotsix-mail-archive"
    assert cfg.archive_enabled is True


def test_archive_defaults_when_unset() -> None:
    """An account without archive overrides keeps the archive defaults."""
    accts = _accounts()
    cfg = accts.default.config
    assert cfg.archive_root == "robotsix-mail-archive"
    assert cfg.archive_enabled is True


def test_archive_custom_values() -> None:
    """archive_root / archive_enabled can be set explicitly."""
    accts = _accounts(archive_root="custom-archive", archive_enabled=False)
    cfg = accts.default.config
    assert cfg.archive_root == "custom-archive"
    assert cfg.archive_enabled is False


def test_archive_enabled_wrong_type() -> None:
    """A non-bool archive_enabled → ValidationError."""
    with pytest.raises(pydantic.ValidationError):  # pydantic ValidationError on type mismatch
        MailConfig(
            imap_host="i",
            smtp_host="s",
            username="u",
            password="p",
            archive_enabled="sometimes",  # type: ignore[arg-type]
        )
