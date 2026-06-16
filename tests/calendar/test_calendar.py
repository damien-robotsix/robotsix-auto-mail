"""Tests for calendar dispatch via TO_CALENDAR column move and dispatch_calendar_request."""

from __future__ import annotations

import os
import sys
import tempfile
from unittest import mock

from tests.server.conftest import (
    _populate_db,
    _post_form,
    _start_test_server,
    _triage_action,
)

from robotsix_auto_mail.calendar import (
    CalendarDispatchError,
    CalendarEventRequest,
    extract_calendar_summary,
    extract_dates_from_body,
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
# HTTP integration tests — POST /move with triage_action=TO_CALENDAR
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


_MOCK_DISPATCH_PATH = (
    "robotsix_auto_mail.calendar.dispatch_calendar_request"
)


def test_move_to_calendar_dispatches_and_reroutes_to_archive() -> None:
    """Moving a card to TO_CALENDAR triggers dispatch and reroutes to
    TO_ARCHIVE when there is no prior TO_ANSWER triage decision."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _setup_db_with_record(db_path)

        with mock.patch(_MOCK_DISPATCH_PATH) as mock_dispatch:
            server, port = _start_test_server(db_path)
            try:
                status, body = _post_form(
                    port,
                    {
                        "message_id": "<cal-test@example.com>",
                        "triage_action": "TO_CALENDAR",
                    },
                    path="/move",
                )
                # Success = 302 redirect (not a JSON response).
                assert status == 302, f"Expected 302, got {status}: {body}"

                # Verify dispatch was called with correct event.
                mock_dispatch.assert_called_once()
                event = mock_dispatch.call_args[0][0]
                assert isinstance(event, CalendarEventRequest)
                assert event.message_id == "<cal-test@example.com>"
                assert event.subject == "Calendar integration test"
                assert event.sender == "alice@example.com"
                assert "2025-06-15" in event.extracted_dates

                # Card should be rerouted to TO_ARCHIVE (no prior TO_ANSWER).
                assert (
                    _triage_action(db_path, "<cal-test@example.com>")
                    == "TO_ARCHIVE"
                )
            finally:
                server.shutdown()
    finally:
        os.unlink(db_path)


def test_move_to_calendar_reroutes_to_answer_when_prior_was_to_answer() -> None:
    """When the prior triage action was TO_ANSWER, successful dispatch
    reroutes back to TO_ANSWER."""
    from robotsix_auto_mail.db import init_db
    from robotsix_auto_mail.triage import set_triage_decision

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _setup_db_with_record(db_path)

        # Seed a prior TO_ANSWER triage decision.
        conn = init_db(db_path)
        try:
            set_triage_decision(
                conn,
                "<cal-test@example.com>",
                "TO_ANSWER",
                source="agent",
                reason="needs reply",
            )
        finally:
            conn.close()

        with mock.patch(_MOCK_DISPATCH_PATH):
            server, port = _start_test_server(db_path)
            try:
                status, _ = _post_form(
                    port,
                    {
                        "message_id": "<cal-test@example.com>",
                        "triage_action": "TO_CALENDAR",
                    },
                    path="/move",
                )
                assert status == 302

                # Card should be rerouted back to TO_ANSWER.
                assert (
                    _triage_action(db_path, "<cal-test@example.com>")
                    == "TO_ANSWER"
                )
            finally:
                server.shutdown()
    finally:
        os.unlink(db_path)


def test_move_to_calendar_missing_message_id_returns_400() -> None:
    """POST /move without message_id returns 400."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        server, port = _start_test_server(db_path)
        try:
            status, body = _post_form(
                port,
                {"triage_action": "TO_CALENDAR"},
                path="/move",
            )
            assert status == 400, f"Expected 400, got {status}: {body}"
            assert "Missing message_id or triage_action" in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_move_to_calendar_unknown_message_id_returns_404() -> None:
    """POST /move with unknown message_id returns 404."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        server, port = _start_test_server(db_path)
        try:
            status, body = _post_form(
                port,
                {
                    "message_id": "<nonexistent@example.com>",
                    "triage_action": "TO_CALENDAR",
                },
                path="/move",
            )
            assert status == 404, f"Expected 404, got {status}: {body}"
            assert "Not found" in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_move_to_calendar_dispatch_error_card_stays() -> None:
    """On CalendarDispatchError, the card stays in TO_CALENDAR (no reroute)."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _setup_db_with_record(db_path)

        with mock.patch(
            _MOCK_DISPATCH_PATH,
            side_effect=CalendarDispatchError("Calendar agent is not available"),
        ):
            server, port = _start_test_server(db_path)
            try:
                status, body = _post_form(
                    port,
                    {
                        "message_id": "<cal-test@example.com>",
                        "triage_action": "TO_CALENDAR",
                    },
                    path="/move",
                )
                # The handler still returns a 302 redirect (move succeeded,
                # calendar dispatch failed — error is on the card indicator).
                assert status == 302, f"Expected 302, got {status}: {body}"

                # Card must remain in TO_CALENDAR.
                assert (
                    _triage_action(db_path, "<cal-test@example.com>")
                    == "TO_CALENDAR"
                )
            finally:
                server.shutdown()
    finally:
        os.unlink(db_path)


def test_move_to_calendar_unexpected_error_card_stays() -> None:
    """On an unexpected exception during dispatch, the card stays in
    TO_CALENDAR (no reroute)."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _setup_db_with_record(db_path)

        with mock.patch(
            _MOCK_DISPATCH_PATH,
            side_effect=RuntimeError("unexpected boom"),
        ):
            server, port = _start_test_server(db_path)
            try:
                status, body = _post_form(
                    port,
                    {
                        "message_id": "<cal-test@example.com>",
                        "triage_action": "TO_CALENDAR",
                    },
                    path="/move",
                )
                assert status == 302, f"Expected 302, got {status}: {body}"

                # Card must remain in TO_CALENDAR.
                assert (
                    _triage_action(db_path, "<cal-test@example.com>")
                    == "TO_CALENDAR"
                )
            finally:
                server.shutdown()
    finally:
        os.unlink(db_path)


def test_move_to_calendar_realistic_message_id() -> None:
    """Moving a card with a Message-ID containing ``<``, ``>``, ``@``,
    ``+``, ``/``, ``=`` resolves the record (no 404) and dispatches.
    """
    message_id = "<abc+def/ghi=123@mail.example.com>"
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _setup_db_with_record(db_path, message_id=message_id)

        with mock.patch(_MOCK_DISPATCH_PATH) as mock_dispatch:
            server, port = _start_test_server(db_path)
            try:
                status, body = _post_form(
                    port,
                    {"message_id": message_id, "triage_action": "TO_CALENDAR"},
                    path="/move",
                )
                assert status == 302, f"Expected 302, got {status}: {body}"
                mock_dispatch.assert_called_once()
                # Card should be rerouted to TO_ARCHIVE.
                assert _triage_action(db_path, message_id) == "TO_ARCHIVE"
            finally:
                server.shutdown()
    finally:
        os.unlink(db_path)


def test_move_to_calendar_angle_bracket_fallback() -> None:
    """Moving a card resolves the record even when the request omits angle
    brackets that the stored message_id includes (or vice versa)."""
    message_id_stored = "<cal-test@example.com>"
    message_id_posted = "cal-test@example.com"
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _setup_db_with_record(db_path, message_id=message_id_stored)

        with mock.patch(_MOCK_DISPATCH_PATH) as mock_dispatch:
            server, port = _start_test_server(db_path)
            try:
                status, body = _post_form(
                    port,
                    {
                        "message_id": message_id_posted,
                        "triage_action": "TO_CALENDAR",
                    },
                    path="/move",
                )
                assert status == 302, f"Expected 302, got {status}: {body}"
                mock_dispatch.assert_called_once()
                # Card should be rerouted to TO_ARCHIVE.
                assert (
                    _triage_action(db_path, message_id_stored) == "TO_ARCHIVE"
                )
            finally:
                server.shutdown()
    finally:
        os.unlink(db_path)


# ============================================================================
# Unit tests — extract_dates_from_body
# ============================================================================


def test_extract_dates_iso() -> None:
    result = extract_dates_from_body("2025-06-15")
    assert result == ["2025-06-15"]


def test_extract_dates_us_slash() -> None:
    result = extract_dates_from_body("6/15/2025")
    assert result == ["6/15/2025"]


def test_extract_dates_dotted() -> None:
    result = extract_dates_from_body("15.06.2025")
    assert result == ["15.06.2025"]


def test_extract_dates_month_name() -> None:
    result = extract_dates_from_body("Jun 15")
    assert result == ["Jun 15"]


def test_extract_dates_month_full() -> None:
    result = extract_dates_from_body("December 25")
    assert result == ["December 25"]


def test_extract_dates_time_12h() -> None:
    result = extract_dates_from_body("3:00 PM")
    assert result == ["3:00 PM"]


def test_extract_dates_time_24h() -> None:
    result = extract_dates_from_body("14:30")
    assert result == ["14:30"]


def test_extract_dates_multiple() -> None:
    result = extract_dates_from_body("2025-06-15 at 3:00 PM and 6/15/2025")
    assert result == ["2025-06-15", "3:00 PM", "6/15/2025"]


def test_extract_dates_empty_string() -> None:
    result = extract_dates_from_body("")
    assert result == []


def test_extract_dates_no_match() -> None:
    result = extract_dates_from_body("No dates here")
    assert result == []


def test_extract_dates_caps_at_10() -> None:
    # 15 ISO dates, only 10 should be returned.
    body = " ".join("2025-06-{:02d}".format(i) for i in range(1, 16))
    result = extract_dates_from_body(body)
    assert len(result) == 10


def test_extract_dates_deduplicates() -> None:
    result = extract_dates_from_body("2025-06-15 2025-06-15")
    assert result == ["2025-06-15"]


# ============================================================================
# Unit tests — extract_calendar_summary
# ============================================================================


def test_summary_includes_subject() -> None:
    from tests.conftest import _make_record

    record = _make_record(subject="Lunch meeting")
    result = extract_calendar_summary(record)
    assert "Subject: Lunch meeting" in result


def test_summary_includes_formatted_date() -> None:
    from tests.conftest import _make_record

    record = _make_record(date="2025-06-15T12:00:00")
    result = extract_calendar_summary(record)
    assert "Email date:" in result
    assert "2025-06-15" in result


def test_summary_includes_extracted_dates() -> None:
    from tests.conftest import _make_record

    record = _make_record(body_plain="Meet on 2025-06-20")
    result = extract_calendar_summary(record)
    assert "Date/time references in body: 2025-06-20" in result


def test_summary_empty_subject_shows_placeholder() -> None:
    from tests.conftest import _make_record

    record = _make_record(subject="")
    result = extract_calendar_summary(record)
    assert "Subject: (no subject)" in result


def test_summary_whitespace_only_subject() -> None:
    from tests.conftest import _make_record

    record = _make_record(subject="   ")
    result = extract_calendar_summary(record)
    assert "Subject: (no subject)" in result


def test_summary_no_body_omits_date_references() -> None:
    from tests.conftest import _make_record

    record = _make_record(body_plain="", body_html="")
    result = extract_calendar_summary(record)
    assert "Date/time references" not in result


def test_summary_no_dates_in_body_omits_date_references() -> None:
    from tests.conftest import _make_record

    record = _make_record(body_plain="Hello world")
    result = extract_calendar_summary(record)
    assert "Date/time references" not in result
