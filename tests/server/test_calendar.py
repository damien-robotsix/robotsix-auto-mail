"""Tests for POST /add-to-calendar and dispatch_calendar_request."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from unittest import mock

from tests.server.conftest import _populate_db, _post_form, _start_test_server

from robotsix_auto_mail.calendar import (
    CalendarDispatchError,
    CalendarEventRequest,
)

# ---------------------------------------------------------------------------
# Helpers — inject/remove fake agent-comm modules
# ---------------------------------------------------------------------------


def _install_fake_agent_comm_modules(
    *,
    agent_side_effect: object = None,
    send_notification_side_effect: object = None,
) -> dict[str, mock.MagicMock]:
    """Install synthetic ``robotsix_agent_comm.*`` modules into ``sys.modules``
    and return mocks keyed by short name.

    When *agent_side_effect* is set, ``robotsix_agent_comm.sdk.Agent`` is
    patched to raise that exception on access (simulating an ImportError
    inside the try/except in ``dispatch_calendar_request``).
    """
    mocks: dict[str, mock.MagicMock] = {}

    # -- Agent mock --
    mock_agent_instance = mock.MagicMock()
    if send_notification_side_effect is not None:
        mock_agent_instance.send_notification.side_effect = (
            send_notification_side_effect
        )
    mock_agent_cls = mock.MagicMock(return_value=mock_agent_instance)

    agent_not_found_error = type("AgentNotFoundError", (Exception,), {})
    delivery_error = type("DeliveryError", (Exception,), {})

    transport_mod = mock.MagicMock()
    transport_mod.AgentNotFoundError = agent_not_found_error
    transport_mod.DeliveryError = delivery_error
    transport_mod.Registry = mock.MagicMock

    sdk_mod = mock.MagicMock()
    sdk_mod.Agent = mock_agent_cls

    # Top-level package module (empty, just needs to exist).
    top_mod = mock.MagicMock()

    sys.modules["robotsix_agent_comm"] = top_mod
    sys.modules["robotsix_agent_comm.sdk"] = sdk_mod
    sys.modules["robotsix_agent_comm.transport"] = transport_mod

    mocks["agent_instance"] = mock_agent_instance
    mocks["agent_cls"] = mock_agent_cls
    mocks["transport"] = transport_mod
    mocks["sdk"] = sdk_mod
    mocks["AgentNotFoundError"] = agent_not_found_error
    mocks["DeliveryError"] = delivery_error
    return mocks


def _remove_fake_agent_comm_modules() -> None:
    for key in list(sys.modules):
        if key.startswith("robotsix_agent_comm"):
            del sys.modules[key]


# ---------------------------------------------------------------------------
# Unit tests — dispatch_calendar_request
# ---------------------------------------------------------------------------


def test_dispatch_calendar_request_success() -> None:
    """dispatch_calendar_request sends a notification via Agent."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    event = CalendarEventRequest(
        message_id="<test@example.com>",
        subject="Test",
        sender="sender@example.com",
        body_text="Body",
        email_date="2025-01-01T00:00:00",
    )

    mocks = _install_fake_agent_comm_modules()
    try:
        dispatch_calendar_request(event)
    finally:
        _remove_fake_agent_comm_modules()

    mocks["agent_instance"].send_notification.assert_called_once_with(
        recipient="robotsix-calendar",
        body=event.model_dump(),
    )


def test_dispatch_import_error() -> None:
    """dispatch_calendar_request raises CalendarDispatchError on ImportError."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    event = CalendarEventRequest(
        message_id="<test@example.com>",
        subject="Test",
        sender="sender@example.com",
        body_text="Body",
        email_date="2025-01-01T00:00:00",
    )

    # Do NOT install fake modules — the import will fail, which is what
    # we want to test.
    try:
        dispatch_calendar_request(event)
        raise AssertionError("expected CalendarDispatchError")
    except CalendarDispatchError as exc:
        assert "Agent communication is not available" in str(exc)


def test_dispatch_agent_not_found_error() -> None:
    """dispatch_calendar_request raises CalendarDispatchError on AgentNotFoundError."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    event = CalendarEventRequest(
        message_id="<test@example.com>",
        subject="Test",
        sender="sender@example.com",
        body_text="Body",
        email_date="2025-01-01T00:00:00",
    )

    mocks = _install_fake_agent_comm_modules()
    # Use the AgentNotFoundError class from the fake transport module so
    # it matches the one dispatch_calendar_request imports at runtime.
    agent_not_found_error = mocks["AgentNotFoundError"]
    mocks["agent_instance"].send_notification.side_effect = agent_not_found_error(
        "robotsix-calendar"
    )
    try:
        try:
            dispatch_calendar_request(event)
            raise AssertionError("expected CalendarDispatchError")
        except CalendarDispatchError as exc:
            assert "Calendar agent is not available" in str(exc)
    finally:
        _remove_fake_agent_comm_modules()


def test_dispatch_delivery_error() -> None:
    """dispatch_calendar_request raises CalendarDispatchError on DeliveryError."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    event = CalendarEventRequest(
        message_id="<test@example.com>",
        subject="Test",
        sender="sender@example.com",
        body_text="Body",
        email_date="2025-01-01T00:00:00",
    )

    mocks = _install_fake_agent_comm_modules()
    delivery_error = mocks["DeliveryError"]
    mocks["agent_instance"].send_notification.side_effect = delivery_error("timeout")
    try:
        try:
            dispatch_calendar_request(event)
            raise AssertionError("expected CalendarDispatchError")
        except CalendarDispatchError as exc:
            assert "Failed to deliver calendar request" in str(exc)
    finally:
        _remove_fake_agent_comm_modules()


def test_dispatch_unexpected_error() -> None:
    """dispatch_calendar_request wraps unexpected exceptions."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    event = CalendarEventRequest(
        message_id="<test@example.com>",
        subject="Test",
        sender="sender@example.com",
        body_text="Body",
        email_date="2025-01-01T00:00:00",
    )

    _ = _install_fake_agent_comm_modules(
        send_notification_side_effect=RuntimeError("boom"),
    )
    try:
        try:
            dispatch_calendar_request(event)
            raise AssertionError("expected CalendarDispatchError")
        except CalendarDispatchError as exc:
            assert "Failed to deliver calendar request" in str(exc)
    finally:
        _remove_fake_agent_comm_modules()


# ---------------------------------------------------------------------------
# HTTP integration tests — POST /add-to-calendar
# ---------------------------------------------------------------------------


def _setup_db_with_record(
    db_path: str,
    message_id: str = "<cal-test@example.com>",
    *,
    body_plain: str = "Meeting on 2025-06-15 at 3:00 PM",
) -> None:
    """Insert a single mail record into *db_path*."""
    _populate_db(
        db_path,
        [
            {
                "message_id": message_id,
                "sender": "alice@example.com",
                "subject": "Calendar integration test",
                "date": "2025-06-10T10:00:00",
                "body_plain": body_plain,
                "status": "to_read",
            },
        ],
    )


def test_add_to_calendar_success() -> None:
    """POST /add-to-calendar with valid message_id returns 200 dispatched."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _setup_db_with_record(db_path)

        with mock.patch(
            "robotsix_auto_mail.server._calendar_mixin.dispatch_calendar_request"
        ) as mock_dispatch:
            server, port = _start_test_server(db_path)
            try:
                status, body = _post_form(
                    port,
                    {"message_id": "<cal-test@example.com>"},
                    path="/add-to-calendar",
                )
                assert status == 200, f"Expected 200, got {status}: {body}"
                payload = json.loads(body)
                assert payload == {"status": "dispatched"}

                # Verify dispatch was called with correct event.
                mock_dispatch.assert_called_once()
                event = mock_dispatch.call_args[0][0]
                assert isinstance(event, CalendarEventRequest)
                assert event.message_id == "<cal-test@example.com>"
                assert event.subject == "Calendar integration test"
                assert event.sender == "alice@example.com"
                assert "2025-06-15" in event.extracted_dates
            finally:
                server.shutdown()
    finally:
        os.unlink(db_path)


def test_add_to_calendar_missing_message_id() -> None:
    """POST /add-to-calendar without message_id returns 400 JSON."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        server, port = _start_test_server(db_path)
        try:
            status, body = _post_form(port, {}, path="/add-to-calendar")
            assert status == 400, f"Expected 400, got {status}: {body}"
            payload = json.loads(body)
            assert payload["status"] == "error"
            assert "Missing message_id" in payload["message"]
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_add_to_calendar_unknown_message_id() -> None:
    """POST /add-to-calendar with unknown message_id returns 404 JSON."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        server, port = _start_test_server(db_path)
        try:
            status, body = _post_form(
                port,
                {"message_id": "<nonexistent@example.com>"},
                path="/add-to-calendar",
            )
            assert status == 404, f"Expected 404, got {status}: {body}"
            payload = json.loads(body)
            assert payload["status"] == "error"
            assert "Not found" in payload["message"]
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_add_to_calendar_dispatch_error() -> None:
    """POST /add-to-calendar returns 502 JSON on CalendarDispatchError."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _setup_db_with_record(db_path)

        with mock.patch(
            "robotsix_auto_mail.server._calendar_mixin.dispatch_calendar_request",
            side_effect=CalendarDispatchError("Calendar agent is not available"),
        ):
            server, port = _start_test_server(db_path)
            try:
                status, body = _post_form(
                    port,
                    {"message_id": "<cal-test@example.com>"},
                    path="/add-to-calendar",
                )
                assert status == 502, f"Expected 502, got {status}: {body}"
                payload = json.loads(body)
                assert payload["status"] == "error"
                assert "Calendar agent is not available" in payload["message"]
            finally:
                server.shutdown()
    finally:
        os.unlink(db_path)


def test_add_to_calendar_dispatch_delivery_error() -> None:
    """POST /add-to-calendar returns 502 JSON with delivery failure message."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _setup_db_with_record(db_path)

        with mock.patch(
            "robotsix_auto_mail.server._calendar_mixin.dispatch_calendar_request",
            side_effect=CalendarDispatchError(
                "Failed to deliver calendar request: timeout"
            ),
        ):
            server, port = _start_test_server(db_path)
            try:
                status, body = _post_form(
                    port,
                    {"message_id": "<cal-test@example.com>"},
                    path="/add-to-calendar",
                )
                assert status == 502, f"Expected 502, got {status}: {body}"
                payload = json.loads(body)
                assert payload["status"] == "error"
                assert "Failed to deliver calendar request" in payload["message"]
            finally:
                server.shutdown()
    finally:
        os.unlink(db_path)


def test_add_to_calendar_unexpected_error() -> None:
    """POST /add-to-calendar returns 500 JSON on unexpected exceptions."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _setup_db_with_record(db_path)

        with mock.patch(
            "robotsix_auto_mail.server._calendar_mixin.dispatch_calendar_request",
            side_effect=RuntimeError("unexpected boom"),
        ):
            server, port = _start_test_server(db_path)
            try:
                status, body = _post_form(
                    port,
                    {"message_id": "<cal-test@example.com>"},
                    path="/add-to-calendar",
                )
                assert status == 500, f"Expected 500, got {status}: {body}"
                payload = json.loads(body)
                assert payload["status"] == "error"
                assert payload["message"] == "Internal error"
            finally:
                server.shutdown()
    finally:
        os.unlink(db_path)
