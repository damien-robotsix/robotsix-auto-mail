"""HTTP request-handler tests for GET /settings and PUT /settings."""

# ruff: noqa: S310 — localhost test URLs

from __future__ import annotations

import json
import urllib.request

from robotsix_auto_mail.config.model import MailConfig
from robotsix_auto_mail.db import init_db
from robotsix_auto_mail.settings import SettingsStore
from tests.server.conftest_helpers import (
    CaptureError,
    NoRedirect,
    _start_test_server,
    _start_test_server_with_mail_config,
)


def _make_mail_config(db_path: str) -> MailConfig:
    """Return a minimal MailConfig for handler tests."""
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        llm_api_key="sk-test-key",
        db_path=db_path,
    )


def _json_get(port: int, path: str) -> tuple[int, dict]:
    """GET *path* and return (status, parsed JSON body)."""
    resp = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}")
    body = resp.read().decode("utf-8")
    return resp.status, json.loads(body)


def _json_post(port: int, path: str, payload: dict) -> tuple[int, dict]:
    """POST JSON *payload* to *path* and return (status, parsed JSON body)."""
    data = json.dumps(payload).encode("utf-8")
    url = f"http://127.0.0.1:{port}{path}"
    opener = urllib.request.build_opener(NoRedirect(), CaptureError())
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    resp = opener.open(req)
    body = resp.read().decode("utf-8")
    return resp.status, json.loads(body)


# ===========================================================================
# GET /settings — empty store (config-file source)
# ===========================================================================


def test_get_settings_empty_store_returns_config_file_source(single_db: str) -> None:
    """When the store is empty, GET /settings derives from MailConfig with secrets masked."""
    cfg = _make_mail_config(single_db)
    server, port = _start_test_server_with_mail_config(single_db, cfg)
    try:
        status, payload = _json_get(port, "/settings")
        assert status == 200
        assert payload["source"] == "config-file"
        settings = payload["settings"]
        # Non-secret fields pass through.
        assert settings["imap_host"] == "imap.example.com"
        assert settings["username"] == "user@example.com"
        # Secret fields are masked.
        assert settings["password"] == "***"
        assert settings["llm_api_key"] == "***"
    finally:
        server.shutdown()


# ===========================================================================
# GET /settings — populated store (internal source)
# ===========================================================================


def test_get_settings_populated_store_returns_internal_source(single_db: str) -> None:
    """When the store has settings, GET /settings returns them with secrets masked."""
    # Seed the store via SettingsStore.
    store = SettingsStore(single_db)
    conn = init_db(single_db)
    try:
        store.update(conn, {"imap_host": "custom.example.com", "password": "new-pass"})
    finally:
        conn.close()

    cfg = _make_mail_config(single_db)
    server, port = _start_test_server_with_mail_config(single_db, cfg)
    try:
        status, payload = _json_get(port, "/settings")
        assert status == 200
        assert payload["source"] == "internal"
        settings = payload["settings"]
        assert settings["imap_host"] == "custom.example.com"
        assert settings["password"] == "***"
    finally:
        server.shutdown()


# ===========================================================================
# PUT /settings — valid updates
# ===========================================================================


def test_put_settings_valid_update(single_db: str) -> None:
    """PUT /settings with valid fields returns 200 and persists them."""
    server, port = _start_test_server(single_db)
    try:
        status, payload = _json_post(
            port, "/settings", {"imap_host": "new.example.com"}
        )
        assert status == 200
        assert payload["ok"] is True
        assert payload["errors"] == {}
    finally:
        server.shutdown()

    # Verify persistence by reading back via GET.
    cfg = _make_mail_config(single_db)
    server2, port2 = _start_test_server_with_mail_config(single_db, cfg)
    try:
        status, payload = _json_get(port2, "/settings")
        assert status == 200
        settings = payload["settings"]
        assert settings["imap_host"] == "new.example.com"
    finally:
        server2.shutdown()


def test_put_settings_secret_masked_on_readback(single_db: str) -> None:
    """Secret values written via PUT are masked when read back."""
    server, port = _start_test_server(single_db)
    try:
        status, payload = _json_post(port, "/settings", {"password": "my-s3cret"})
        assert status == 200
    finally:
        server.shutdown()

    cfg = _make_mail_config(single_db)
    server2, port2 = _start_test_server_with_mail_config(single_db, cfg)
    try:
        status, payload = _json_get(port2, "/settings")
        assert status == 200
        assert payload["settings"]["password"] == "***"
    finally:
        server2.shutdown()


# ===========================================================================
# PUT /settings — validation / rejection
# ===========================================================================


def test_put_settings_unknown_field_rejected(single_db: str) -> None:
    """PUT /settings with an unknown field returns 422 with error details."""
    server, port = _start_test_server(single_db)
    try:
        status, payload = _json_post(port, "/settings", {"bad_key": "value"})
        assert status == 422
        assert payload["ok"] is False
        assert "bad_key" in payload["errors"]
        assert "unknown setting" in payload["errors"]["bad_key"]
    finally:
        server.shutdown()


def test_put_settings_validation_error(single_db: str) -> None:
    """PUT /settings with an invalid value returns 422."""
    server, port = _start_test_server(single_db)
    try:
        status, payload = _json_post(port, "/settings", {"imap_tls_mode": "INVALID"})
        assert status == 422
        assert payload["ok"] is False
        assert "imap_tls_mode" in payload["errors"]
    finally:
        server.shutdown()


def test_put_settings_partial_success(single_db: str) -> None:
    """When some fields are valid and some are not, valid fields persist."""
    server, port = _start_test_server(single_db)
    try:
        status, payload = _json_post(
            port, "/settings", {"imap_host": "good.example.com", "bad_key": "value"}
        )
        assert status == 422
        assert payload["ok"] is False
        assert "bad_key" in payload["errors"]
        # imap_host was valid and should not appear in errors.
        assert "imap_host" not in payload["errors"]
    finally:
        server.shutdown()

    # Verify the valid field persisted.
    cfg = _make_mail_config(single_db)
    server2, port2 = _start_test_server_with_mail_config(single_db, cfg)
    try:
        status, payload = _json_get(port2, "/settings")
        assert status == 200
        assert payload["settings"]["imap_host"] == "good.example.com"
    finally:
        server2.shutdown()


# ===========================================================================
# PUT /settings — body errors
# ===========================================================================


def test_put_settings_empty_body(single_db: str) -> None:
    """PUT /settings with an empty body returns 400."""
    server, port = _start_test_server(single_db)
    try:
        url = f"http://127.0.0.1:{port}/settings"
        opener = urllib.request.build_opener(NoRedirect(), CaptureError())
        req = urllib.request.Request(
            url, data=b"", method="POST", headers={"Content-Type": "application/json"}
        )
        resp = opener.open(req)
        body = resp.read().decode("utf-8")
        payload = json.loads(body)
        assert resp.status == 400
        assert payload["ok"] is False
        assert "_body" in payload["errors"]
    finally:
        server.shutdown()


def test_put_settings_invalid_json(single_db: str) -> None:
    """PUT /settings with invalid JSON returns 400."""
    server, port = _start_test_server(single_db)
    try:
        url = f"http://127.0.0.1:{port}/settings"
        opener = urllib.request.build_opener(NoRedirect(), CaptureError())
        req = urllib.request.Request(
            url,
            data=b"not json",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        resp = opener.open(req)
        body = resp.read().decode("utf-8")
        payload = json.loads(body)
        assert resp.status == 400
        assert payload["ok"] is False
        assert "_body" in payload["errors"]
    finally:
        server.shutdown()


def test_put_settings_non_object_json(single_db: str) -> None:
    """PUT /settings with a JSON array (not object) returns 400."""
    server, port = _start_test_server(single_db)
    try:
        url = f"http://127.0.0.1:{port}/settings"
        opener = urllib.request.build_opener(NoRedirect(), CaptureError())
        req = urllib.request.Request(
            url,
            data=b"[1,2,3]",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        resp = opener.open(req)
        body = resp.read().decode("utf-8")
        payload = json.loads(body)
        assert resp.status == 400
        assert payload["ok"] is False
        assert "_body" in payload["errors"]
    finally:
        server.shutdown()
