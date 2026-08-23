"""HTTP request-handler tests for the standard config surface.

Exercises the routes end to end — including that they are reachable before an
account is selected, and that ``PUT`` is wired at all — rather than calling the
mixin directly (``test_settings_mixin.py`` covers that layer).
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from robotsix_auto_mail.config.model import MailConfig
from tests.server.conftest_helpers import (
    CaptureError,
    NoRedirect,
    _start_test_server_with_mail_config,
)


def _make_mail_config(db_path: str) -> MailConfig:
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        llm_api_key="sk-test-key",
        db_path=db_path,
    )


@pytest.fixture
def config_file(tmp_path: Path, single_db: str) -> Iterator[Path]:
    """A throwaway config file holding one account."""
    path = tmp_path / "config" / "config.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "account_id": "work",
                        "config": {
                            "imap_host": "imap.example.com",
                            "smtp_host": "smtp.example.com",
                            "username": "user@example.com",
                            "password": "s3cret",
                            "db_path": single_db,
                        },
                    }
                ],
            }
        )
    )
    with mock.patch.dict(os.environ, {"ROBOTSIX_CONFIG_FILE": str(path)}):
        yield path


def _json_get(port: int, path: str) -> tuple[int, dict[str, Any]]:
    resp = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}")
    return resp.status, json.loads(resp.read().decode("utf-8"))


def _json_request(
    port: int, method: str, path: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    opener = urllib.request.build_opener(NoRedirect(), CaptureError())
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method=method,
        headers={"Content-Type": "application/json"},
    )
    resp = opener.open(req)
    return resp.status, json.loads(resp.read().decode("utf-8"))


def test_get_config_serves_masked_config_and_schema(
    single_db: str, config_file: Path
) -> None:
    server, port = _start_test_server_with_mail_config(
        single_db, _make_mail_config(single_db)
    )
    try:
        status, payload = _json_get(port, "/config")
        assert status == 200
        assert payload["schema"]["properties"]["accounts"]
        assert payload["config"]["accounts"][0]["config"]["password"] == "**********"
        assert "s3cret" not in json.dumps(payload)
    finally:
        server.shutdown()


def test_put_config_is_routed_and_persists(single_db: str, config_file: Path) -> None:
    server, port = _start_test_server_with_mail_config(
        single_db, _make_mail_config(single_db)
    )
    try:
        status, payload = _json_request(
            port,
            "PUT",
            "/config",
            {"accounts": [{"account_id": "work", "config": {"imap_folder": "Later"}}]},
        )
        assert status == 200
        assert payload["version"] >= 1

        stored = json.loads(config_file.read_text())
        assert stored["accounts"][0]["config"]["imap_folder"] == "Later"
        # The untouched secret survives the write.
        assert stored["accounts"][0]["config"]["password"] == "s3cret"
    finally:
        server.shutdown()


def test_put_config_rejects_invalid_values(single_db: str, config_file: Path) -> None:
    server, port = _start_test_server_with_mail_config(
        single_db, _make_mail_config(single_db)
    )
    try:
        status, payload = _json_request(
            port,
            "PUT",
            "/config",
            {"accounts": [{"account_id": "work", "config": {"imap_port": "nope"}}]},
        )
        assert status == 422
        assert payload["type"] == "urn:robotsix:error:config-validation"
    finally:
        server.shutdown()


def test_versions_and_rollback_routes(single_db: str, config_file: Path) -> None:
    server, port = _start_test_server_with_mail_config(
        single_db, _make_mail_config(single_db)
    )
    try:
        _json_request(
            port,
            "PUT",
            "/config",
            {"accounts": [{"account_id": "work", "config": {"imap_folder": "Later"}}]},
        )

        status, payload = _json_get(port, "/config/versions")
        assert status == 200
        assert payload["versions"]

        status, _ = _json_request(port, "POST", "/config/rollback", {"version": 1})
        assert status == 200
        stored = json.loads(config_file.read_text())
        assert stored["accounts"][0]["config"]["imap_folder"] == "INBOX"
    finally:
        server.shutdown()


def test_settings_page_serves_the_shared_panel(single_db: str) -> None:
    server, port = _start_test_server_with_mail_config(
        single_db, _make_mail_config(single_db)
    )
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/settings-panel")
        body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert "/static/settings-loader.js" in body
        assert "/static/appshell-loader.js" in body
    finally:
        server.shutdown()


def test_removed_settings_routes_are_gone(single_db: str) -> None:
    server, port = _start_test_server_with_mail_config(
        single_db, _make_mail_config(single_db)
    )
    try:
        opener = urllib.request.build_opener(NoRedirect(), CaptureError())
        resp = opener.open(f"http://127.0.0.1:{port}/settings")
        assert resp.status == 404
    finally:
        server.shutdown()


def test_vendored_panel_asset_is_served_when_present(
    single_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from robotsix_auto_mail.server import _view_mixin

    monkeypatch.setattr(_view_mixin, "_STATIC_ROBOTSIX_UI_JS", "export const ok = 1;")
    server, port = _start_test_server_with_mail_config(
        single_db, _make_mail_config(single_db)
    )
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/static/robotsix-ui.js")
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/javascript")
    finally:
        server.shutdown()


def test_missing_vendored_panel_asset_404s(
    single_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkout without the vendored build must degrade, not crash."""
    from robotsix_auto_mail.server import _view_mixin

    monkeypatch.setattr(_view_mixin, "_STATIC_ROBOTSIX_UI_JS", None)
    server, port = _start_test_server_with_mail_config(
        single_db, _make_mail_config(single_db)
    )
    try:
        opener = urllib.request.build_opener(NoRedirect(), CaptureError())
        resp = opener.open(f"http://127.0.0.1:{port}/static/robotsix-ui.js")
        assert resp.status == 404
    finally:
        server.shutdown()
