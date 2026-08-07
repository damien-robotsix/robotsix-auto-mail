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
    path.write_text(json.dumps({"accounts": [_account("work")]}))
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
        }
    )
    # Submit the same two accounts in the opposite order, with masked secrets.
    reordered = []
    for account_id in ("home", "work"):
        entry = _account(account_id)
        entry["config"]["password"] = MASK
        reordered.append(entry)
    update_config({"accounts": reordered})

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
        {"accounts": [_account("work")]},
    )

    account = stripped["accounts"][0]["config"]
    assert "password" not in account
    # A field is secret because the model types it SecretStr, not because its
    # name looks secret — oauth2_client_id stays.
    assert account["username"] == "work@example.com"


def test_merge_updates_leaves_unmentioned_keys_alone() -> None:
    merged = merge_updates(
        MailAccountsConfig,
        {"accounts": [_account("work")], "triage_level": 2},
        {"triage_level": 2},
    )
    assert merged["accounts"][0]["config"]["username"] == "work@example.com"


# ---------------------------------------------------------------------------
# The canonical credential blocks (dict-valued fields)
# ---------------------------------------------------------------------------

_BLOCKS: dict[str, Any] = {
    "langfuse": {
        "host": "https://langfuse.example.net",
        "projects": {
            "robotsix-auto-mail": {
                "public_key": "pk-lf",
                "secret_key": "sk-lf",
                "project_id": "cm1",
            }
        },
    },
    "openrouter": {"keys": {"robotsix-auto-mail": "sk-or"}},
}


@pytest.fixture
def config_file_with_blocks(tmp_path: Path) -> Iterator[Path]:
    """A config file that declares both canonical credential blocks."""
    path = tmp_path / "config" / "config.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "accounts": [_account("work")],
                **_BLOCKS,
            }
        )
    )
    with mock.patch.dict(os.environ, {"ROBOTSIX_CONFIG_FILE": str(path)}):
        yield path


def test_secrets_inside_a_map_are_masked_on_read(config_file_with_blocks: Path) -> None:
    """A secret nested in a map is still a typed secret, so it is never echoed."""
    config = get_config()["config"]

    assert config["langfuse"]["projects"]["robotsix-auto-mail"]["secret_key"] == MASK
    assert config["openrouter"]["keys"]["robotsix-auto-mail"] == MASK
    # Non-secret siblings are readable.
    assert config["langfuse"]["projects"]["robotsix-auto-mail"]["public_key"] == "pk-lf"


def test_blank_map_secrets_preserve_the_stored_values(
    config_file_with_blocks: Path,
) -> None:
    """The panel sends a blank secret for anything the operator did not retype."""
    update_config(
        {
            "langfuse": {
                "projects": {
                    "robotsix-auto-mail": {"public_key": "pk-lf-2", "secret_key": ""}
                }
            },
            "openrouter": {"keys": {"robotsix-auto-mail": MASK}},
        }
    )

    stored = _stored(config_file_with_blocks)
    project = stored["langfuse"]["projects"]["robotsix-auto-mail"]
    assert project["public_key"] == "pk-lf-2"
    assert project["secret_key"] == "sk-lf"
    assert stored["openrouter"]["keys"]["robotsix-auto-mail"] == "sk-or"


def test_removing_a_map_entry_removes_it(config_file_with_blocks: Path) -> None:
    """The submitted map is authoritative — an alias left out is deleted.

    Merged key-by-key like an object, a removed alias would survive every
    save and the operator could never retire a project.
    """
    update_config({"langfuse": {"projects": {}}, "openrouter": {"keys": {}}})

    stored = _stored(config_file_with_blocks)
    assert stored["langfuse"]["projects"] == {}
    assert stored["openrouter"]["keys"] == {}
    # A sibling scalar in the same block is untouched.
    assert stored["langfuse"]["host"] == "https://langfuse.example.net"


def test_adding_an_alias_keeps_the_existing_one(config_file_with_blocks: Path) -> None:
    update_config(
        {
            "openrouter": {
                "keys": {"robotsix-auto-mail": MASK, "robotsix-auto-mail-x": "sk-or-x"}
            }
        }
    )

    keys = _stored(config_file_with_blocks)["openrouter"]["keys"]
    assert keys == {"robotsix-auto-mail": "sk-or", "robotsix-auto-mail-x": "sk-or-x"}


def test_history_never_stores_a_secret_nested_in_a_map(
    config_file_with_blocks: Path,
) -> None:
    """Version history holds no secrets — including the ones inside maps."""
    update_config({"langfuse": {"host": "https://langfuse.example.org"}})

    history = json.dumps(list_versions())
    assert "sk-lf" not in history
    assert "sk-or" not in history
    # The *path* of a changed secret is still recorded, so a rotation is visible.
    update_config({"openrouter": {"keys": {"robotsix-auto-mail": "sk-or-rotated"}}})
    changed = list_versions()["versions"][0]["changed_keys"]
    assert "openrouter.keys.robotsix-auto-mail (secret)" in changed
    assert "sk-or-rotated" not in json.dumps(list_versions())


def test_rollback_keeps_map_secrets(config_file_with_blocks: Path) -> None:
    """History has no map secrets, so rolling back must not wipe them."""
    update_config({"langfuse": {"host": "https://langfuse.example.org"}})
    first = list_versions()["versions"][-1]["version"]

    rollback(first)

    stored = _stored(config_file_with_blocks)
    assert stored["openrouter"]["keys"]["robotsix-auto-mail"] == "sk-or"
    assert stored["langfuse"]["projects"]["robotsix-auto-mail"]["secret_key"] == "sk-lf"
