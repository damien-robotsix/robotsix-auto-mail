"""Unit tests for the config surface handlers in ``_SettingsMixin``.

These exercise the HTTP shell over ``robotsix_auto_mail.config.service``:
status codes, the ``problem+json`` envelope, and the accounts-cache refresh
that keeps a running server from serving pre-write config.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from robotsix_auto_mail.server._settings_mixin import _SettingsMixin


class _FakeServer:
    """Stands in for the HTTPServer, exposing the handler-factory keywords."""

    def __init__(self, keywords: dict[str, Any] | None = None) -> None:
        self.RequestHandlerClass = mock.MagicMock()
        self.RequestHandlerClass.keywords = keywords if keywords is not None else {}


class _FakeConfigHandler(_SettingsMixin):
    """Concrete handler wiring protocol stubs for direct mixin testing."""

    def __init__(self, body: bytes = b"", server: _FakeServer | None = None) -> None:
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.server = server or _FakeServer()
        self._serve_json = mock.MagicMock()
        self._send_response = mock.MagicMock()

    @property
    def payload(self) -> Any:
        assert self._serve_json.call_args is not None
        return self._serve_json.call_args[0][0]

    @property
    def status(self) -> int:
        assert self._serve_json.call_args is not None
        status: int = self._serve_json.call_args[1]["status"]
        return status


def _account(account_id: str = "work") -> dict[str, Any]:
    return {
        "account_id": account_id,
        "config": {
            "imap_host": "imap.example.com",
            "smtp_host": "smtp.example.com",
            "username": f"{account_id}@example.com",
            "password": "stored-secret",
            "db_path": f"/tmp/{account_id}.db",  # noqa: S108
        },
    }


@pytest.fixture
def config_file(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "config" / "config.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"accounts": [_account()]}))
    with mock.patch.dict(os.environ, {"ROBOTSIX_CONFIG_FILE": str(path)}):
        yield path


def _json_body(payload: Any) -> bytes:
    return json.dumps(payload).encode()


# ---------------------------------------------------------------------------
# GET /config
# ---------------------------------------------------------------------------


def test_get_config_returns_config_schema_and_version(config_file: Path) -> None:
    handler = _FakeConfigHandler()

    handler._handle_get_config()

    assert handler.status == 200
    assert handler.payload["config"]["accounts"][0]["account_id"] == "work"
    assert handler.payload["schema"]["properties"]["accounts"]
    assert handler.payload["version"] >= 1


def test_get_config_never_echoes_a_secret(config_file: Path) -> None:
    handler = _FakeConfigHandler()

    handler._handle_get_config()

    assert "stored-secret" not in json.dumps(handler.payload)


# ---------------------------------------------------------------------------
# PUT /config
# ---------------------------------------------------------------------------


def test_put_config_applies_the_update_and_refreshes_the_cache(
    config_file: Path,
) -> None:
    server = _FakeServer({"accounts": None})
    handler = _FakeConfigHandler(
        _json_body(
            {"accounts": [{"account_id": "work", "config": {"imap_folder": "Later"}}]}
        ),
        server=server,
    )

    handler._handle_put_config()

    assert handler.status == 200
    stored = json.loads(config_file.read_text())
    assert stored["accounts"][0]["config"]["imap_folder"] == "Later"
    # The running server must not keep serving the pre-write config.
    assert server.RequestHandlerClass.keywords["accounts"].ids() == ("work",)


def test_put_config_reports_validation_failures_as_problem_json(
    config_file: Path,
) -> None:
    before = config_file.read_text()
    handler = _FakeConfigHandler(
        _json_body(
            {"accounts": [{"account_id": "work", "config": {"imap_tls_mode": "bogus"}}]}
        )
    )

    handler._handle_put_config()

    assert handler.status == 422
    assert handler.payload["type"] == "urn:robotsix:error:config-validation"
    assert "imap_tls_mode" in handler.payload["detail"]
    assert config_file.read_text() == before


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"", "empty request body"),
        (b"{not json", "invalid JSON"),
        (b'["a"]', "expected a JSON object"),
    ],
)
def test_put_config_rejects_malformed_bodies(
    config_file: Path, body: bytes, expected: str
) -> None:
    handler = _FakeConfigHandler(body)

    handler._handle_put_config()

    assert handler.status == 400
    assert expected in handler.payload["detail"]


# ---------------------------------------------------------------------------
# GET /config/versions and POST /config/rollback
# ---------------------------------------------------------------------------


def test_get_config_versions_lists_history(config_file: Path) -> None:
    _FakeConfigHandler(
        _json_body(
            {"accounts": [{"account_id": "work", "config": {"imap_folder": "Later"}}]}
        )
    )._handle_put_config()

    handler = _FakeConfigHandler()
    handler._handle_get_config_versions()

    assert handler.status == 200
    versions = handler.payload["versions"]
    assert versions[0]["version"] > versions[-1]["version"]
    assert "accounts.0.config.imap_folder" in versions[0]["changed_keys"]


def test_rollback_restores_a_previous_version(config_file: Path) -> None:
    _FakeConfigHandler(
        _json_body(
            {"accounts": [{"account_id": "work", "config": {"imap_folder": "Later"}}]}
        )
    )._handle_put_config()

    handler = _FakeConfigHandler(_json_body({"version": 1}))
    handler._handle_config_rollback()

    assert handler.status == 200
    stored = json.loads(config_file.read_text())
    assert stored["accounts"][0]["config"]["imap_folder"] == "INBOX"
    # History carries no secrets, so the live one survives the rollback.
    assert stored["accounts"][0]["config"]["password"] == "stored-secret"


@pytest.mark.parametrize("version", ["1", None, True])
def test_rollback_requires_an_integer_version(config_file: Path, version: Any) -> None:
    handler = _FakeConfigHandler(_json_body({"version": version}))

    handler._handle_config_rollback()

    assert handler.status == 422
    assert "must be an integer" in handler.payload["detail"]


def test_rollback_to_an_unknown_version_is_rejected(config_file: Path) -> None:
    handler = _FakeConfigHandler(_json_body({"version": 99}))

    handler._handle_config_rollback()

    assert handler.status == 422


# ---------------------------------------------------------------------------
# The Settings page
# ---------------------------------------------------------------------------


def test_settings_page_mounts_the_shared_panel() -> None:
    handler = _FakeConfigHandler()

    handler._serve_settings_panel()

    body = handler._send_response.call_args[0][0]
    # No bespoke form: the page mounts the shared renderer against the surface.
    assert "/static/robotsix-ui.js" in body
    assert "mountConfigPanel" in body
    assert "<input" not in body


def test_put_config_mirrors_into_the_settings_store(
    config_file: Path, tmp_path: Path
) -> None:
    """The recovery snapshot must track the operator's latest save."""
    import sqlite3

    from robotsix_auto_mail.settings import SettingsStore

    db_path = str(tmp_path / "mail.db")
    handler = _FakeConfigHandler(
        _json_body(
            {
                "accounts": [
                    {
                        "account_id": "work",
                        "config": {"db_path": db_path, "imap_folder": "Later"},
                    }
                ]
            }
        )
    )

    handler._handle_put_config()

    assert handler.status == 200
    conn = sqlite3.connect(db_path)
    try:
        stored = SettingsStore(db_path).get_all(conn)
    finally:
        conn.close()
    assert stored["imap_folder"] == "Later"
    # The store masks secrets on read, exactly as it did before.
    assert stored["password"] == "***"
