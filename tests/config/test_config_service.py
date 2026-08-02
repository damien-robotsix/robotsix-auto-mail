"""Tests for the standard config surface (``robotsix_auto_mail.config.service``).

Covers the semantics robotsix-standards ``config-ownership.md`` requires:
masked reads, partial updates, typed-secret merge-on-write, versioning with
secret-free history, and rollback.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from robotsix_auto_mail.config import service
from robotsix_auto_mail.config.model import MailAccountsConfig
from robotsix_auto_mail.config.service import (
    MASK,
    ConfigValidationError,
    get_config,
    list_versions,
    masked_config,
    merge_updates,
    rollback,
    strip_secrets,
    update_config,
)


def _account(account_id: str, host: str = "imap.example.com") -> dict[str, Any]:
    return {
        "account_id": account_id,
        "config": {
            "imap_host": host,
            "smtp_host": "smtp.example.com",
            "username": f"{account_id}@example.com",
            "password": f"{account_id}-secret",
            "db_path": f"/tmp/{account_id}.db",  # noqa: S108
        },
    }


@pytest.fixture
def config_file(tmp_path: Path) -> Iterator[Path]:
    """Point the config library at a throwaway config file."""
    path = tmp_path / "config" / "config.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"accounts": [_account("work")], "default_account_id": "work"})
    )
    with mock.patch.dict(os.environ, {"ROBOTSIX_CONFIG_FILE": str(path)}):
        yield path


def _stored(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text())
    return data


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_get_config_masks_secrets_and_returns_schema(config_file: Path) -> None:
    result = get_config()

    account = result["config"]["accounts"][0]
    assert account["config"]["password"] == MASK
    assert account["config"]["imap_host"] == "imap.example.com"
    # The schema the panel renders is the committed one for the root model.
    assert result["schema"]["properties"]["accounts"]
    # The first read seeds version 1 so there is always something to roll back to.
    assert result["version"] == 1


def test_get_config_survives_an_unreadable_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json")
    with mock.patch.dict(os.environ, {"ROBOTSIX_CONFIG_FILE": str(path)}):
        assert get_config()["config"]["accounts"] == []


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def test_update_applies_only_the_submitted_keys(config_file: Path) -> None:
    update_config(
        {
            "accounts": [
                {"account_id": "work", "config": {"imap_host": "new.example.com"}}
            ]
        }
    )

    stored = _stored(config_file)["accounts"][0]["config"]
    assert stored["imap_host"] == "new.example.com"
    # Everything not submitted keeps its stored value.
    assert stored["username"] == "work@example.com"
    assert stored["smtp_host"] == "smtp.example.com"


def test_blank_and_masked_secrets_preserve_the_stored_value(config_file: Path) -> None:
    for submitted in ("", MASK):
        update_config(
            {
                "accounts": [
                    {
                        "account_id": "work",
                        "config": {"password": submitted, "imap_folder": "Other"},
                    }
                ]
            }
        )
        stored = _stored(config_file)["accounts"][0]["config"]
        assert stored["password"] == "work-secret"
        assert stored["imap_folder"] == "Other"


def test_an_explicit_secret_overwrites_the_stored_one(config_file: Path) -> None:
    update_config(
        {"accounts": [{"account_id": "work", "config": {"password": "rotated"}}]}
    )

    assert _stored(config_file)["accounts"][0]["config"]["password"] == "rotated"
    # …and is still never echoed back.
    assert masked_config()["accounts"][0]["config"]["password"] == MASK


def test_invalid_update_is_rejected_and_changes_nothing(config_file: Path) -> None:
    before = _stored(config_file)

    with pytest.raises(ConfigValidationError) as excinfo:
        update_config(
            {
                "accounts": [
                    {"account_id": "work", "config": {"imap_tls_mode": "nonsense"}}
                ]
            }
        )

    # The detail names the offending field so the panel can place it inline.
    assert "imap_tls_mode" in excinfo.value.detail
    assert excinfo.value.key is not None
    assert excinfo.value.key.endswith("imap_tls_mode")
    assert _stored(config_file) == before


def test_secrets_stay_with_their_own_account_when_reordered(config_file: Path) -> None:
    update_config(
        {
            "accounts": [_account("work"), _account("home")],
            "default_account_id": "work",
        }
    )
    # Submit the same two accounts in the opposite order, with masked secrets.
    reordered = []
    for account_id in ("home", "work"):
        entry = _account(account_id)
        entry["config"]["password"] = MASK
        reordered.append(entry)
    update_config({"accounts": reordered, "default_account_id": "work"})

    stored = {
        a["account_id"]: a["config"]["password"]
        for a in _stored(config_file)["accounts"]
    }
    assert stored == {"home": "home-secret", "work": "work-secret"}


# ---------------------------------------------------------------------------
# Versions and rollback
# ---------------------------------------------------------------------------


def test_history_records_changed_keys_but_never_secret_values(
    config_file: Path,
) -> None:
    update_config(
        {
            "accounts": [
                {
                    "account_id": "work",
                    "config": {"imap_host": "new.example.com", "password": "rotated"},
                }
            ]
        }
    )

    versions = list_versions()["versions"]
    assert versions[0]["version"] == 2
    changed = versions[0]["changed_keys"]
    assert "accounts.0.config.imap_host" in changed
    assert "accounts.0.config.password (secret)" in changed

    raw = service._version_store().path.read_text()
    assert "rotated" not in raw
    assert "work-secret" not in raw


def test_rollback_restores_values_and_keeps_current_secrets(config_file: Path) -> None:
    get_config()  # seed version 1
    update_config(
        {
            "accounts": [
                {
                    "account_id": "work",
                    "config": {"imap_host": "new.example.com", "password": "rotated"},
                }
            ]
        }
    )

    result = rollback(1)

    stored = _stored(config_file)["accounts"][0]["config"]
    assert stored["imap_host"] == "imap.example.com"
    # History holds no secrets, so rolling back must not wipe the live one.
    assert stored["password"] == "rotated"
    # Rollback is itself a versioned write.
    assert result["version"] == 3


def test_rollback_to_an_unknown_version_is_rejected(config_file: Path) -> None:
    with pytest.raises(ConfigValidationError):
        rollback(99)


def test_history_is_capped(config_file: Path) -> None:
    from robotsix_auto_mail.config.versions import RETENTION

    for index in range(RETENTION + 3):
        update_config(
            {
                "accounts": [
                    {"account_id": "work", "config": {"imap_folder": f"F{index}"}}
                ]
            }
        )

    versions = list_versions()["versions"]
    assert len(versions) == RETENTION
    # Newest first.
    assert versions[0]["version"] > versions[-1]["version"]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_strip_secrets_removes_typed_secret_leaves_only() -> None:
    stripped = strip_secrets(
        MailAccountsConfig,
        {"accounts": [_account("work")], "default_account_id": "work"},
    )

    account = stripped["accounts"][0]["config"]
    assert "password" not in account
    # A field is secret because the model types it SecretStr, not because its
    # name looks secret — oauth2_client_id stays.
    assert account["username"] == "work@example.com"


def test_merge_updates_leaves_unmentioned_keys_alone() -> None:
    merged = merge_updates(
        MailAccountsConfig,
        {"accounts": [_account("work")], "default_account_id": "work"},
        {"default_account_id": "work"},
    )
    assert merged["accounts"][0]["config"]["username"] == "work@example.com"
