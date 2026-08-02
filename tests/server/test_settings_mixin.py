"""Unit tests for _SettingsMixin._handle_get_settings and _handle_put_settings."""

from __future__ import annotations

import io
import os
import tempfile
from unittest import mock

from pydantic import SecretStr

from robotsix_auto_mail.config.model import MailConfig
from robotsix_auto_mail.server._settings_mixin import _SettingsMixin


class _FakeSettingsHandler(_SettingsMixin):
    """Concrete handler wiring protocol stubs for direct mixin testing."""

    def __init__(
        self,
        *,
        db_path: str,
        mail_config: MailConfig | None = None,
        headers: dict[str, str] | None = None,
        rfile: io.BytesIO | None = None,
    ) -> None:
        self.db_path = db_path
        self.mail_config = mail_config
        self.headers = headers or {}
        self.rfile = rfile or io.BytesIO()
        self._serve_json = mock.MagicMock()


def _make_mail_config(
    db_path: str = "/tmp/test.db",  # noqa: S108
) -> MailConfig:
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password=SecretStr("secret"),
        db_path=db_path,
    )


# ---------------------------------------------------------------------------
# GET /settings — empty store (config-file source)
# ---------------------------------------------------------------------------


def test_handle_get_settings_empty_store_returns_config_file_source() -> None:
    """When the store is empty, settings are derived from MailConfig with
    secrets masked, and source is 'config-file'."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        cfg = _make_mail_config(db_path)
        handler = _FakeSettingsHandler(db_path=db_path, mail_config=cfg)
        handler._handle_get_settings()

        handler._serve_json.assert_called_once()
        call_args = handler._serve_json.call_args
        assert call_args is not None
        args, kwargs = call_args
        payload = args[0]
        assert kwargs["status"] == 200
        assert payload["source"] == "config-file"
        settings = payload["settings"]
        assert settings["imap_host"] == "imap.example.com"
        assert settings["username"] == "user@example.com"
        assert settings["password"] == "***"
    finally:
        os.unlink(db_path)


def test_handle_get_settings_empty_store_null_config_returns_empty() -> None:
    """When the store is empty AND mail_config is None, returns empty settings."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        handler = _FakeSettingsHandler(db_path=db_path, mail_config=None)
        handler._handle_get_settings()

        handler._serve_json.assert_called_once()
        call_args = handler._serve_json.call_args
        assert call_args is not None
        args, kwargs = call_args
        payload = args[0]
        assert kwargs["status"] == 200
        assert payload["source"] == "config-file"
        assert payload["settings"] == {}
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# GET /settings — populated store (internal source)
# ---------------------------------------------------------------------------


def test_handle_get_settings_populated_store_returns_internal_source() -> None:
    """When the store has settings, they are returned with secrets masked
    and source is 'internal'."""
    from robotsix_auto_mail.db import init_db
    from robotsix_auto_mail.settings import SettingsStore

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # Seed the store with some settings.
        store = SettingsStore(db_path)
        conn = init_db(db_path)
        try:
            store.update(
                conn, {"imap_host": "custom.example.com", "password": "new-pass"}
            )
        finally:
            conn.close()

        cfg = _make_mail_config(db_path)
        handler = _FakeSettingsHandler(db_path=db_path, mail_config=cfg)
        handler._handle_get_settings()

        handler._serve_json.assert_called_once()
        call_args = handler._serve_json.call_args
        assert call_args is not None
        args, kwargs = call_args
        payload = args[0]
        assert kwargs["status"] == 200
        assert payload["source"] == "internal"
        settings = payload["settings"]
        assert settings["imap_host"] == "custom.example.com"
        assert settings["password"] == "***"
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# PUT /settings — valid updates
# ---------------------------------------------------------------------------


def test_handle_put_settings_valid_update_returns_ok() -> None:
    """PUT /settings with valid fields returns 200 ok."""
    import json

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        body = json.dumps({"imap_host": "new.example.com"}).encode("utf-8")
        handler = _FakeSettingsHandler(
            db_path=db_path,
            headers={"Content-Length": str(len(body))},
            rfile=io.BytesIO(body),
        )
        handler._handle_put_settings()

        handler._serve_json.assert_called_once()
        call_args = handler._serve_json.call_args
        assert call_args is not None
        args, kwargs = call_args
        payload = args[0]
        assert kwargs["status"] == 200
        assert payload["ok"] is True
        assert payload["errors"] == {}
    finally:
        os.unlink(db_path)


def test_handle_put_settings_persists_and_reads_back() -> None:
    """Valid fields written via PUT are persisted and readable via GET."""
    import json

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # Write via PUT.
        body = json.dumps({"imap_host": "persist.example.com"}).encode("utf-8")
        handler = _FakeSettingsHandler(
            db_path=db_path,
            headers={"Content-Length": str(len(body))},
            rfile=io.BytesIO(body),
        )
        handler._handle_put_settings()
        assert handler._serve_json.call_args is not None
        _args, kwargs = handler._serve_json.call_args
        assert kwargs["status"] == 200

        # Read back via GET.
        handler2 = _FakeSettingsHandler(
            db_path=db_path, mail_config=_make_mail_config(db_path)
        )
        handler2._handle_get_settings()
        call_args = handler2._serve_json.call_args
        assert call_args is not None
        args2, _ = call_args
        payload = args2[0]
        assert payload["settings"]["imap_host"] == "persist.example.com"
    finally:
        os.unlink(db_path)


def test_handle_put_settings_secret_masked_on_readback() -> None:
    """Secret values written via PUT are masked when read back."""
    import json

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        body = json.dumps({"password": "my-s3cret"}).encode("utf-8")
        handler = _FakeSettingsHandler(
            db_path=db_path,
            headers={"Content-Length": str(len(body))},
            rfile=io.BytesIO(body),
        )
        handler._handle_put_settings()
        assert handler._serve_json.call_args is not None
        _args, kwargs = handler._serve_json.call_args
        assert kwargs["status"] == 200

        # Read back via GET.
        handler2 = _FakeSettingsHandler(
            db_path=db_path, mail_config=_make_mail_config(db_path)
        )
        handler2._handle_get_settings()
        call_args = handler2._serve_json.call_args
        assert call_args is not None
        args2, _ = call_args
        payload = args2[0]
        assert payload["settings"]["password"] == "***"
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# PUT /settings — validation / rejection
# ---------------------------------------------------------------------------


def test_handle_put_settings_unknown_field_rejected() -> None:
    """PUT /settings with an unknown field returns 422 with error details."""
    import json

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        body = json.dumps({"bad_key": "value"}).encode("utf-8")
        handler = _FakeSettingsHandler(
            db_path=db_path,
            headers={"Content-Length": str(len(body))},
            rfile=io.BytesIO(body),
        )
        handler._handle_put_settings()

        handler._serve_json.assert_called_once()
        call_args = handler._serve_json.call_args
        assert call_args is not None
        args, kwargs = call_args
        payload = args[0]
        assert kwargs["status"] == 422
        assert payload["ok"] is False
        assert "bad_key" in payload["errors"]
        assert "unknown setting" in payload["errors"]["bad_key"]
    finally:
        os.unlink(db_path)


def test_handle_put_settings_validation_error() -> None:
    """PUT /settings with an invalid value returns 422."""
    import json

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        body = json.dumps({"imap_tls_mode": "INVALID"}).encode("utf-8")
        handler = _FakeSettingsHandler(
            db_path=db_path,
            headers={"Content-Length": str(len(body))},
            rfile=io.BytesIO(body),
        )
        handler._handle_put_settings()

        handler._serve_json.assert_called_once()
        call_args = handler._serve_json.call_args
        assert call_args is not None
        args, kwargs = call_args
        payload = args[0]
        assert kwargs["status"] == 422
        assert payload["ok"] is False
        assert "imap_tls_mode" in payload["errors"]
    finally:
        os.unlink(db_path)


def test_handle_put_settings_partial_success() -> None:
    """When some fields are valid and some are not, valid fields persist
    and errors list only the rejected keys."""
    import json

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        body = json.dumps({"imap_host": "good.example.com", "bad_key": "value"}).encode(
            "utf-8"
        )
        handler = _FakeSettingsHandler(
            db_path=db_path,
            headers={"Content-Length": str(len(body))},
            rfile=io.BytesIO(body),
        )
        handler._handle_put_settings()

        handler._serve_json.assert_called_once()
        call_args = handler._serve_json.call_args
        assert call_args is not None
        args, kwargs = call_args
        payload = args[0]
        assert kwargs["status"] == 422
        assert payload["ok"] is False
        assert "bad_key" in payload["errors"]
        assert "imap_host" not in payload["errors"]

        # Verify the valid field persisted.
        handler2 = _FakeSettingsHandler(
            db_path=db_path, mail_config=_make_mail_config(db_path)
        )
        handler2._handle_get_settings()
        call_args2 = handler2._serve_json.call_args
        assert call_args2 is not None
        args2, _ = call_args2
        payload2 = args2[0]
        assert payload2["settings"]["imap_host"] == "good.example.com"
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# PUT /settings — body errors
# ---------------------------------------------------------------------------


def test_handle_put_settings_empty_body_returns_400() -> None:
    """PUT /settings with Content-Length=0 (empty body) returns 400."""
    handler = _FakeSettingsHandler(
        db_path="/tmp/test.db",  # noqa: S108
        headers={"Content-Length": "0"},
    )
    handler._handle_put_settings()

    handler._serve_json.assert_called_once()
    call_args = handler._serve_json.call_args
    assert call_args is not None
    args, kwargs = call_args
    payload = args[0]
    assert kwargs["status"] == 400
    assert payload["ok"] is False
    assert "_body" in payload["errors"]


def test_handle_put_settings_missing_content_length_returns_400() -> None:
    """PUT /settings with no Content-Length header defaults to 0 → 400."""
    handler = _FakeSettingsHandler(db_path="/tmp/test.db")  # noqa: S108
    handler._handle_put_settings()

    handler._serve_json.assert_called_once()
    call_args = handler._serve_json.call_args
    assert call_args is not None
    args, kwargs = call_args
    payload = args[0]
    assert kwargs["status"] == 400
    assert payload["ok"] is False
    assert "_body" in payload["errors"]


def test_handle_put_settings_invalid_json_returns_400() -> None:
    """PUT /settings with unparseable JSON returns 400."""
    body = b"not json"
    handler = _FakeSettingsHandler(
        db_path="/tmp/test.db",  # noqa: S108
        headers={"Content-Length": str(len(body))},
        rfile=io.BytesIO(body),
    )
    handler._handle_put_settings()

    handler._serve_json.assert_called_once()
    call_args = handler._serve_json.call_args
    assert call_args is not None
    args, kwargs = call_args
    payload = args[0]
    assert kwargs["status"] == 400
    assert payload["ok"] is False
    assert "_body" in payload["errors"]


def test_handle_put_settings_non_object_json_returns_400() -> None:
    """PUT /settings with a JSON array (not object) returns 400."""
    body = b"[1,2,3]"
    handler = _FakeSettingsHandler(
        db_path="/tmp/test.db",  # noqa: S108
        headers={"Content-Length": str(len(body))},
        rfile=io.BytesIO(body),
    )
    handler._handle_put_settings()

    handler._serve_json.assert_called_once()
    call_args = handler._serve_json.call_args
    assert call_args is not None
    args, kwargs = call_args
    payload = args[0]
    assert kwargs["status"] == 400
    assert payload["ok"] is False
    assert "_body" in payload["errors"]
