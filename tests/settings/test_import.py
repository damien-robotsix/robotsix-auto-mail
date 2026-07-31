"""Unit tests for ``robotsix_auto_mail.settings.import_``."""

from __future__ import annotations

import json
import os
import sqlite3
from unittest import mock

import pytest

from robotsix_auto_mail.settings import import_ as settings_import
from robotsix_auto_mail.settings.store import SettingsStore


# ===========================================================================
# _fetch_export
# ===========================================================================


def _make_urlopen_mock(json_body: dict[str, object], code: int = 200) -> mock.MagicMock:
    """Build a fake ``urlopen`` context manager returning *json_body*."""
    fake_response = mock.MagicMock()
    fake_response.read.return_value = json.dumps(json_body).encode("utf-8")
    fake_response.__enter__.return_value = fake_response
    return mock.MagicMock(return_value=fake_response)


def test_fetch_export_flat_dict() -> None:
    """A flat JSON object is returned as-is."""
    body = {"imap_host": "mail.example.com", "username": "u"}
    mock_urlopen = _make_urlopen_mock(body)

    with mock.patch("urllib.request.urlopen", mock_urlopen):
        result = settings_import._fetch_export("https://example.com/export")

    assert result == body
    mock_urlopen.assert_called_once()


def test_fetch_export_nested_config() -> None:
    """``{"config": {...}}`` is unwrapped to the inner dict."""
    inner = {"imap_host": "mail.example.com"}
    mock_urlopen = _make_urlopen_mock({"config": inner})

    with mock.patch("urllib.request.urlopen", mock_urlopen):
        result = settings_import._fetch_export("https://example.com/export")

    assert result == inner


def test_fetch_export_nested_config_non_dict_ignored() -> None:
    """When "config" exists but is NOT a dict, the outer dict is returned."""
    body: dict[str, object] = {"config": "not-a-dict", "other": 1}
    mock_urlopen = _make_urlopen_mock(body)

    with mock.patch("urllib.request.urlopen", mock_urlopen):
        result = settings_import._fetch_export("https://example.com/export")

    assert result == body


def test_fetch_export_returns_dict() -> None:
    """When the nested "config" dict is present and is a dict, it is returned."""
    inner = {"smtp_host": "smtp.example.com"}
    body: dict[str, object] = {"config": inner, "extra": "ignored"}
    mock_urlopen = _make_urlopen_mock(body)

    with mock.patch("urllib.request.urlopen", mock_urlopen):
        result = settings_import._fetch_export("https://example.com/export")

    assert result == inner


def test_fetch_export_value_error_on_non_dict() -> None:
    """A JSON array (list) raises ValueError."""
    mock_urlopen = _make_urlopen_mock([1, 2, 3])  # type: ignore[arg-type]

    with mock.patch("urllib.request.urlopen", mock_urlopen):
        with pytest.raises(ValueError, match="expected a JSON object"):
            settings_import._fetch_export("https://example.com/export")


def test_fetch_export_value_error_on_string() -> None:
    """A JSON string (not an object) raises ValueError."""
    fake_response = mock.MagicMock()
    fake_response.read.return_value = b'"just a string"'
    fake_response.__enter__.return_value = fake_response
    mock_urlopen = mock.MagicMock(return_value=fake_response)

    with mock.patch("urllib.request.urlopen", mock_urlopen):
        with pytest.raises(ValueError, match="expected a JSON object"):
            settings_import._fetch_export("https://example.com/export")


def test_fetch_export_json_decode_error() -> None:
    """Invalid JSON in the response body raises ``json.JSONDecodeError``."""
    fake_response = mock.MagicMock()
    fake_response.read.return_value = b"not json"
    fake_response.__enter__.return_value = fake_response
    mock_urlopen = mock.MagicMock(return_value=fake_response)

    with mock.patch("urllib.request.urlopen", mock_urlopen):
        with pytest.raises(json.JSONDecodeError):
            settings_import._fetch_export("https://example.com/export")


def test_fetch_export_os_error() -> None:
    """Network-level failures raise ``OSError``."""
    mock_urlopen = mock.MagicMock(side_effect=OSError("Connection refused"))

    with mock.patch("urllib.request.urlopen", mock_urlopen):
        with pytest.raises(OSError, match="Connection refused"):
            settings_import._fetch_export("https://example.com/export")


# ===========================================================================
# import_from_central_deploy — env-var gate & empty-store guard
# ===========================================================================


def test_import_skips_when_env_var_not_set(conn: sqlite3.Connection) -> None:
    """Returns ``False`` when ``CENTRAL_DEPLOY_EXPORT_URL`` is absent."""
    store = SettingsStore(":memory:")
    with mock.patch.dict(os.environ, {}, clear=True):
        result = settings_import.import_from_central_deploy(store, conn)
    assert result is False


def test_import_skips_when_env_var_empty_string(conn: sqlite3.Connection) -> None:
    """Returns ``False`` when the env var is set to an empty string."""
    store = SettingsStore(":memory:")
    with mock.patch.dict(os.environ, {"CENTRAL_DEPLOY_EXPORT_URL": ""}):
        result = settings_import.import_from_central_deploy(store, conn)
    assert result is False


def test_import_skips_when_store_not_empty(conn: sqlite3.Connection) -> None:
    """Returns ``False`` when the store is already populated."""
    store = SettingsStore(":memory:")
    with mock.patch.object(store, "is_empty", return_value=False):
        with mock.patch.dict(os.environ, {"CENTRAL_DEPLOY_EXPORT_URL": "https://example.com/export"}):
            result = settings_import.import_from_central_deploy(store, conn)
    assert result is False


def test_import_raises_type_error_for_non_settings_store(conn: sqlite3.Connection) -> None:
    """Raises ``TypeError`` when *store* is not a ``SettingsStore``."""
    with mock.patch.dict(os.environ, {"CENTRAL_DEPLOY_EXPORT_URL": "https://example.com/export"}):
        with pytest.raises(TypeError, match="expects a SettingsStore"):
            settings_import.import_from_central_deploy("not-a-store", conn)


def test_import_raises_type_error_for_none_store(conn: sqlite3.Connection) -> None:
    """Raises ``TypeError`` when *store* is ``None``."""
    with mock.patch.dict(os.environ, {"CENTRAL_DEPLOY_EXPORT_URL": "https://example.com/export"}):
        with pytest.raises(TypeError, match="expects a SettingsStore"):
            settings_import.import_from_central_deploy(None, conn)


def test_type_check_before_empty_check() -> None:
    """TypeError is raised before the empty-check, even when env var is absent."""
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(TypeError):
            settings_import.import_from_central_deploy(None, mock.MagicMock())


# ===========================================================================
# import_from_central_deploy — fetch failures
# ===========================================================================


def test_import_returns_false_on_fetch_error(conn: sqlite3.Connection) -> None:
    """Returns ``False`` (not an exception) when ``_fetch_export`` raises."""
    with mock.patch.dict(os.environ, {"CENTRAL_DEPLOY_EXPORT_URL": "https://example.com/export"}):
        with mock.patch.object(
            settings_import, "_fetch_export", side_effect=OSError("timeout")
        ):
            result = settings_import.import_from_central_deploy(
                SettingsStore(":memory:"), conn
            )
    assert result is False


def test_import_returns_false_on_json_error(conn: sqlite3.Connection) -> None:
    """Returns ``False`` when the export returns invalid JSON."""
    with mock.patch.dict(os.environ, {"CENTRAL_DEPLOY_EXPORT_URL": "https://example.com/export"}):
        with mock.patch.object(
            settings_import,
            "_fetch_export",
            side_effect=json.JSONDecodeError("bad json", "", 0),
        ):
            result = settings_import.import_from_central_deploy(
                SettingsStore(":memory:"), conn
            )
    assert result is False


# ===========================================================================
# import_from_central_deploy — seeding success paths
# ===========================================================================


def test_import_seeds_recognised_settings(conn: sqlite3.Connection) -> None:
    """Valid MailConfig keys are filtered and stored via ``set_component_settings``."""
    export = {
        "imap_host": "mail.example.com",
        "smtp_host": "smtp.example.com",
        "username": "user@example.com",
        "password": "s3cret",
        "unknown_key": "should-be-filtered",
    }
    with mock.patch.dict(os.environ, {"CENTRAL_DEPLOY_EXPORT_URL": "https://example.com/export"}):
        with mock.patch.object(settings_import, "_fetch_export", return_value=export):
            with mock.patch(
                "robotsix_auto_mail.db.set_component_settings"
            ) as mock_set:
                result = settings_import.import_from_central_deploy(
                    SettingsStore(":memory:"), conn
                )

    assert result is True
    mock_set.assert_called_once()
    args, _kwargs = mock_set.call_args
    filtered = args[1]  # second positional arg = settings dict
    assert "imap_host" in filtered
    assert "smtp_host" in filtered
    assert "username" in filtered
    assert "password" in filtered
    assert "unknown_key" not in filtered
    # All values are converted to str.
    assert filtered["imap_host"] == "mail.example.com"


def test_import_filters_empty_result(conn: sqlite3.Connection) -> None:
    """Returns ``False`` when export contains no recognised MailConfig keys."""
    export = {"completely_unknown": "val", "also_unknown": 42}
    with mock.patch.dict(os.environ, {"CENTRAL_DEPLOY_EXPORT_URL": "https://example.com/export"}):
        with mock.patch.object(settings_import, "_fetch_export", return_value=export):
            with mock.patch(
                "robotsix_auto_mail.db.set_component_settings"
            ) as mock_set:
                result = settings_import.import_from_central_deploy(
                    SettingsStore(":memory:"), conn
                )

    assert result is False
    mock_set.assert_not_called()


def test_import_seeds_nested_config_response(conn: sqlite3.Connection) -> None:
    """_fetch_export's nested ``{"config": {...}}`` unwrapping is transparent."""
    inner = {"imap_host": "nested.example.com", "username": "nested-user"}
    with mock.patch.dict(os.environ, {"CENTRAL_DEPLOY_EXPORT_URL": "https://example.com/export"}):
        # Simulate what _fetch_export returns for a nested response.
        with mock.patch.object(settings_import, "_fetch_export", return_value=inner):
            with mock.patch(
                "robotsix_auto_mail.db.set_component_settings"
            ) as mock_set:
                result = settings_import.import_from_central_deploy(
                    SettingsStore(":memory:"), conn
                )

    assert result is True
    mock_set.assert_called_once()
    _args, _kwargs = mock_set.call_args
    filtered = _args[1]
    assert filtered["imap_host"] == "nested.example.com"


def test_import_converts_values_to_str(conn: sqlite3.Connection) -> None:
    """Integer and boolean values from export are stringified before storing."""
    export = {
        "imap_host": "mail.example.com",
        "smtp_host": "smtp.example.com",
        "username": "u",
        "imap_port": 993,  # int → str
        "archive_enabled": True,  # bool → str
    }
    with mock.patch.dict(os.environ, {"CENTRAL_DEPLOY_EXPORT_URL": "https://example.com/export"}):
        with mock.patch.object(settings_import, "_fetch_export", return_value=export):
            with mock.patch(
                "robotsix_auto_mail.db.set_component_settings"
            ) as mock_set:
                result = settings_import.import_from_central_deploy(
                    SettingsStore(":memory:"), conn
                )

    assert result is True
    mock_set.assert_called_once()
    _args, _kwargs = mock_set.call_args
    filtered = _args[1]
    assert filtered["imap_port"] == "993"
    assert filtered["archive_enabled"] == "True"
