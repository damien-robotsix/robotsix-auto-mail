"""Unit tests for calendar/listener.py — background listener for
CalendarEventResponse notifications."""

from __future__ import annotations

import logging
import sys
from unittest import mock

import pytest

from robotsix_auto_mail.calendar.listener import (
    _handle_calendar_response,
    start_calendar_listener,
    stop_calendar_listener,
)
from robotsix_auto_mail.calendar.schema import CalendarEventResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_body(**overrides: object) -> dict[str, object]:
    """Return a minimal valid CalendarEventResponse body dict."""
    body: dict[str, object] = {
        "correlation_id": "corr-001",
        "status": "success",
        "event_ref": "https://cal.example.com/evt/42",
    }
    body.update(overrides)  # type: ignore[arg-type]
    return body


def _patch_db(monkeypatch) -> mock.MagicMock:
    """Install mocks for the DB functions imported inside
    _handle_calendar_response and return the mock connection."""
    mock_conn = mock.MagicMock()
    mock_init_db = mock.MagicMock(return_value=mock_conn)
    mock_get = mock.MagicMock()
    mock_update = mock.MagicMock()

    monkeypatch.setattr("robotsix_auto_mail.db.init_db", mock_init_db, raising=False)
    monkeypatch.setattr(
        "robotsix_auto_mail.db.get_record_by_correlation_id",
        mock_get,
        raising=False,
    )
    monkeypatch.setattr(
        "robotsix_auto_mail.db.update_calendar_event_ref",
        mock_update,
        raising=False,
    )

    # Attach the mocks to the connection for easy access in tests.
    mock_conn._mocks = {  # type: ignore[attr-defined]
        "init_db": mock_init_db,
        "get_record_by_correlation_id": mock_get,
        "update_calendar_event_ref": mock_update,
    }
    return mock_conn


def _install_fake_agent_comm_modules() -> dict[str, mock.MagicMock]:
    """Install synthetic ``robotsix_agent_comm.*`` modules into ``sys.modules``."""
    mocks: dict[str, mock.MagicMock] = {}

    mock_agent_instance = mock.MagicMock()
    mock_agent_cls = mock.MagicMock(return_value=mock_agent_instance)

    transport_mod = mock.MagicMock()
    transport_mod.Registry = mock.MagicMock

    sdk_mod = mock.MagicMock()
    sdk_mod.Agent = mock_agent_cls

    sys.modules["robotsix_agent_comm"] = mock.MagicMock()
    sys.modules["robotsix_agent_comm.sdk"] = sdk_mod
    sys.modules["robotsix_agent_comm.transport"] = transport_mod

    mocks["agent_instance"] = mock_agent_instance
    mocks["agent_cls"] = mock_agent_cls
    mocks["transport"] = transport_mod
    mocks["sdk"] = sdk_mod
    return mocks


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_agent_comm_modules() -> None:
    """Ensure no stale fake modules leak between tests."""
    for key in list(sys.modules):
        if key.startswith("robotsix_agent_comm"):
            del sys.modules[key]
    yield
    for key in list(sys.modules):
        if key.startswith("robotsix_agent_comm"):
            del sys.modules[key]


# ---------------------------------------------------------------------------
# Layer 1 — Pure unit tests for _handle_calendar_response
# ---------------------------------------------------------------------------


def test_handle_success_with_event_ref(monkeypatch) -> None:
    """status="success" with event_ref → update called with that ref."""
    mock_conn = _patch_db(monkeypatch)
    mocks = mock_conn._mocks  # type: ignore[attr-defined]

    from tests.conftest import _make_record

    record = _make_record(message_id="<msg@test.com>")
    mocks["get_record_by_correlation_id"].return_value = record

    body = _make_body(status="success", event_ref="cal://evt/1")
    _handle_calendar_response(":memory:", body)

    mocks["init_db"].assert_called_once_with(":memory:", skip_migrations=True)
    mocks["get_record_by_correlation_id"].assert_called_once_with(mock_conn, "corr-001")
    mocks["update_calendar_event_ref"].assert_called_once_with(
        mock_conn, "<msg@test.com>", "cal://evt/1"
    )
    mock_conn.close.assert_called_once()


def test_handle_success_without_event_ref(monkeypatch) -> None:
    """status="success" and no event_ref → defaults to "success"."""
    mock_conn = _patch_db(monkeypatch)
    mocks = mock_conn._mocks  # type: ignore[attr-defined]

    from tests.conftest import _make_record

    record = _make_record(message_id="<msg@test.com>")
    mocks["get_record_by_correlation_id"].return_value = record

    body = _make_body(status="success", event_ref="")
    _handle_calendar_response(":memory:", body)

    mocks["update_calendar_event_ref"].assert_called_once_with(
        mock_conn, "<msg@test.com>", "success"
    )


def test_handle_error_with_message(monkeypatch) -> None:
    """status="error" with message → ref = "error: <message>"."""
    mock_conn = _patch_db(monkeypatch)
    mocks = mock_conn._mocks  # type: ignore[attr-defined]

    from tests.conftest import _make_record

    record = _make_record(message_id="<msg@test.com>")
    mocks["get_record_by_correlation_id"].return_value = record

    body = _make_body(status="error", event_ref="", message="Calendar full")
    _handle_calendar_response(":memory:", body)

    mocks["update_calendar_event_ref"].assert_called_once_with(
        mock_conn, "<msg@test.com>", "error: Calendar full"
    )


def test_handle_error_without_message(monkeypatch) -> None:
    """status="error" and no message → ref = "error: Unknown error"."""
    mock_conn = _patch_db(monkeypatch)
    mocks = mock_conn._mocks  # type: ignore[attr-defined]

    from tests.conftest import _make_record

    record = _make_record(message_id="<msg@test.com>")
    mocks["get_record_by_correlation_id"].return_value = record

    body = _make_body(status="error", event_ref="", message="")
    _handle_calendar_response(":memory:", body)

    mocks["update_calendar_event_ref"].assert_called_once_with(
        mock_conn, "<msg@test.com>", "error: Unknown error"
    )


def test_handle_malformed_body_no_db_calls(monkeypatch, caplog) -> None:
    """Non-CalendarEventResponse payload → logged and ignored (no DB)."""
    mock_conn = _patch_db(monkeypatch)
    mocks = mock_conn._mocks  # type: ignore[attr-defined]

    # Missing required field "correlation_id" → validation fails.
    with caplog.at_level(logging.DEBUG):
        _handle_calendar_response(":memory:", {"status": "success"})

    mocks["init_db"].assert_not_called()
    mocks["get_record_by_correlation_id"].assert_not_called()
    mocks["update_calendar_event_ref"].assert_not_called()
    assert "Ignoring non-CalendarEventResponse notification" in caplog.text


def test_handle_missing_record_no_update(monkeypatch, caplog) -> None:
    """Valid response but no matching record → logged and skipped."""
    mock_conn = _patch_db(monkeypatch)
    mocks = mock_conn._mocks  # type: ignore[attr-defined]
    mocks["get_record_by_correlation_id"].return_value = None

    with caplog.at_level(logging.DEBUG):
        _handle_calendar_response(":memory:", _make_body())

    mocks["update_calendar_event_ref"].assert_not_called()
    assert "No MailRecord found" in caplog.text
    # Connection still closed in finally.
    mock_conn.close.assert_called_once()


def test_handle_connection_closed_on_error(monkeypatch) -> None:
    """DB connection is closed in finally even when an exception occurs."""
    mock_conn = _patch_db(monkeypatch)
    mocks = mock_conn._mocks  # type: ignore[attr-defined]
    # Raise inside the try block so the finally still runs.
    mocks["get_record_by_correlation_id"].side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _handle_calendar_response(":memory:", _make_body())

    mock_conn.close.assert_called_once()


def test_handle_valid_response_uses_real_schema(monkeypatch) -> None:
    """A body matching CalendarEventResponse is accepted without mocking
    the schema class (integration with the real Pydantic model)."""
    mock_conn = _patch_db(monkeypatch)
    mocks = mock_conn._mocks  # type: ignore[attr-defined]

    from tests.conftest import _make_record

    record = _make_record(message_id="<real@test.com>")
    mocks["get_record_by_correlation_id"].return_value = record

    body = CalendarEventResponse(
        correlation_id="corr-real",
        status="success",
        event_ref="evt-ref",
    ).model_dump()
    _handle_calendar_response(":memory:", body)

    mocks["update_calendar_event_ref"].assert_called_once_with(
        mock_conn, "<real@test.com>", "evt-ref"
    )


# ---------------------------------------------------------------------------
# Layer 2 — Thread-launch tests for start / stop
# ---------------------------------------------------------------------------


def test_start_missing_sdk_returns_none() -> None:
    """When robotsix_agent_comm is not installed, returns None."""
    import builtins

    # Remove cached modules and block re-import so the
    # lazy import inside start_calendar_listener raises ImportError.
    saved = {}
    for key in list(sys.modules):
        if key.startswith("robotsix_agent_comm"):
            saved[key] = sys.modules.pop(key)

    _orig_import = builtins.__import__

    def _block_agent_comm(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("robotsix_agent_comm"):
            raise ImportError(f"No module named {name!r}")
        return _orig_import(name, *args, **kwargs)

    with mock.patch("builtins.__import__", new=_block_agent_comm):
        try:
            handle = start_calendar_listener(":memory:")
            assert handle is None
        finally:
            sys.modules.update(saved)


def test_start_available_sdk_returns_handle() -> None:
    """With SDK installed, returns a handle and the thread is alive."""
    _install_fake_agent_comm_modules()

    # Mock _handle_calendar_response so the loop doesn't try real DB I/O.
    with mock.patch("robotsix_auto_mail.calendar.listener._handle_calendar_response"):
        handle = start_calendar_listener(":memory:")

    assert handle is not None
    thread, _agent = handle
    assert thread.is_alive()


def test_stop_none_is_noop() -> None:
    """stop_calendar_listener(None) does nothing."""
    stop_calendar_listener(None)


def test_stop_with_handle_no_crash() -> None:
    """stop_calendar_listener(handle) does not crash."""
    _install_fake_agent_comm_modules()

    with mock.patch("robotsix_auto_mail.calendar.listener._handle_calendar_response"):
        handle = start_calendar_listener(":memory:")

    # Should not raise.
    stop_calendar_listener(handle)
