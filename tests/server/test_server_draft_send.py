"""Tests for draft sending operations (send-draft reply/forward/validation/buttons)."""

from __future__ import annotations

from unittest import mock
from urllib.request import urlopen

from robotsix_auto_mail.db import init_db
from tests.server._draft_helpers import _patch_smtp_and_imap
from tests.server.conftest_helpers import (
    _dummy_send_mail_config,
    _post_to_path,
    _seed_archive_override,
    _seed_draft_record,
    _start_test_server,
    _start_test_server_with_mail_config,
)


def test_send_draft_reply_sends_and_requeues_for_triage(single_db: str) -> None:
    """POST /send-draft reply → sends mail, retains the record, stores the
    sent reply, and clears the triage decision so it re-enters triage."""

    from robotsix_auto_mail.db import (
        get_record_by_message_id,
        list_untriaged_records,
    )

    _seed_draft_record(
        single_db,
        "send-reply-mid",
        sender="alice@other.com",
        subject="Question",
        draft_text="Here is my reply.",
        imap_uid=5,
    )
    _seed_archive_override(single_db, "send-reply-mid", "")

    server, port = _start_test_server_with_mail_config(
        single_db, _dummy_send_mail_config()
    )
    try:
        with _patch_smtp_and_imap() as (smtp_cls, imap_cls):
            resp = _post_to_path(
                port,
                "/send-draft",
                {"message_id": "send-reply-mid", "reply_mode": "reply"},
            )

            assert resp.status == 302
            assert resp.headers.get("Location") == "/board"

            send_mock = smtp_cls.return_value.__enter__.return_value.send
            send_mock.assert_called_once()
            kwargs = send_mock.call_args.kwargs
            assert kwargs["from_addr"] == "user@example.com"
            assert kwargs["to_addr"] == "alice@other.com"
            assert kwargs["subject"].startswith("Re: ")
            assert kwargs["body"] == "Here is my reply."
            assert not kwargs["cc"]

            # No IMAP archive move was performed in the send path.
            imap_cls.return_value.__enter__.return_value.move_message.assert_not_called()

        # The record is retained, its sent reply body is stored, and its
        # triage decision was cleared so it appears as untriaged.
        conn = init_db(single_db)
        try:
            record = get_record_by_message_id(conn, "send-reply-mid")
            assert record is not None
            assert record.sent_reply_text == "Here is my reply."
            untriaged_ids = {r.message_id for r in list_untriaged_records(conn)}
            assert "send-reply-mid" in untriaged_ids
        finally:
            conn.close()
    finally:
        server.shutdown()


def test_send_draft_forward_sends_and_requeues(single_db: str) -> None:
    """POST /send-draft forward → sends mail to forward_to, retains record,
    stores sent reply, and clears triage decision."""

    from robotsix_auto_mail.db import (
        get_record_by_message_id,
        list_untriaged_records,
    )

    _seed_draft_record(
        single_db,
        "send-fwd-mid",
        sender="alice@other.com",
        subject="FYI thread",
        draft_text="Forwarding this.",
        imap_uid=7,
    )
    _seed_archive_override(single_db, "send-fwd-mid", "")

    server, port = _start_test_server_with_mail_config(
        single_db, _dummy_send_mail_config()
    )
    try:
        with _patch_smtp_and_imap() as (smtp_cls, imap_cls):
            resp = _post_to_path(
                port,
                "/send-draft",
                {
                    "message_id": "send-fwd-mid",
                    "reply_mode": "forward",
                    "forward_to": "fwd@example.com",
                },
            )

            assert resp.status == 302
            assert resp.headers.get("Location") == "/board"

            send_mock = smtp_cls.return_value.__enter__.return_value.send
            send_mock.assert_called_once()
            kwargs = send_mock.call_args.kwargs
            assert kwargs["to_addr"] == "fwd@example.com"
            assert kwargs["subject"].startswith("Fwd: ")
            assert kwargs["in_reply_to"] is None
            assert kwargs["references"] is None

            # No IMAP archive move was performed.
            imap_cls.return_value.__enter__.return_value.move_message.assert_not_called()

        # Record retained, sent reply body stored, triage decision cleared.
        conn = init_db(single_db)
        try:
            record = get_record_by_message_id(conn, "send-fwd-mid")
            assert record is not None
            assert record.sent_reply_text == "Forwarding this."
            untriaged_ids = {r.message_id for r in list_untriaged_records(conn)}
            assert "send-fwd-mid" in untriaged_ids
        finally:
            conn.close()
    finally:
        server.shutdown()


def test_send_draft_refuses_self_reply(single_db: str) -> None:
    """POST /send-draft where the recipient equals the user's own address →
    400 and no SMTP send occurs."""

    _seed_draft_record(
        single_db,
        "send-self-mid",
        sender="USER@example.com",  # same address as username, differing case
        subject="Note to self",
        draft_text="Reply body.",
        imap_uid=5,
    )

    server, port = _start_test_server_with_mail_config(
        single_db, _dummy_send_mail_config()
    )
    try:
        with mock.patch("robotsix_auto_mail.smtp.SmtpClient") as smtp_cls:
            resp = _post_to_path(
                port,
                "/send-draft",
                {"message_id": "send-self-mid", "reply_mode": "reply"},
            )
            assert resp.status == 400
            smtp_cls.return_value.__enter__.return_value.send.assert_not_called()
    finally:
        server.shutdown()


def test_send_draft_reply_all_cc_recipients(single_db: str) -> None:
    """POST /send-draft reply_all → cc is original to+cc minus self/sender."""

    _seed_draft_record(
        single_db,
        "send-all-mid",
        sender="sender@test.com",
        subject="Group thread",
        draft_text="Reply to everyone.",
        recipients_json=(
            '{"to": ["sender@test.com", "carol@x.com", "user@example.com"],'
            ' "cc": ["dave@x.com", "Carol@x.com"]}'
        ),
        imap_uid=9,
    )
    _seed_archive_override(single_db, "send-all-mid", "")

    server, port = _start_test_server_with_mail_config(
        single_db, _dummy_send_mail_config()
    )
    try:
        with _patch_smtp_and_imap() as (smtp_cls, _imap_cls):
            resp = _post_to_path(
                port,
                "/send-draft",
                {"message_id": "send-all-mid", "reply_mode": "reply_all"},
            )

            assert resp.status == 302
            send_mock = smtp_cls.return_value.__enter__.return_value.send
            kwargs = send_mock.call_args.kwargs
            # Self (username) and the sender (already in To) are
            # excluded; duplicates removed; order preserved.
            assert kwargs["cc"] == ["carol@x.com", "dave@x.com"]
    finally:
        server.shutdown()


def test_send_draft_subject_not_double_prefixed(single_db: str) -> None:
    """A subject already starting with 'Re:' is not double-prefixed."""

    _seed_draft_record(
        single_db,
        "send-re-mid",
        sender="alice@other.com",
        subject="RE: existing",
        draft_text="Reply body.",
        imap_uid=2,
    )
    _seed_archive_override(single_db, "send-re-mid", "")

    server, port = _start_test_server_with_mail_config(
        single_db, _dummy_send_mail_config()
    )
    try:
        with _patch_smtp_and_imap() as (smtp_cls, _imap_cls):
            _post_to_path(
                port,
                "/send-draft",
                {"message_id": "send-re-mid", "reply_mode": "reply"},
            )

            send_mock = smtp_cls.return_value.__enter__.return_value.send
            assert send_mock.call_args.kwargs["subject"] == "RE: existing"
    finally:
        server.shutdown()


def test_send_draft_validation_errors(single_db: str) -> None:
    """Validation failures return 4xx and never send/archive/delete."""

    _seed_draft_record(
        single_db,
        "send-valid-mid",
        sender="alice@other.com",
        subject="Subject",
        draft_text="A draft.",
        imap_uid=4,
    )
    # A record with an empty draft for the empty-draft check.
    _seed_draft_record(
        single_db,
        "send-empty-mid",
        sender="alice@other.com",
        subject="Subject",
        draft_text="   ",
        imap_uid=6,
    )

    server, port = _start_test_server_with_mail_config(
        single_db, _dummy_send_mail_config()
    )
    try:
        with _patch_smtp_and_imap() as (smtp_cls, imap_cls):
            # Missing message_id → 400.
            assert (
                _post_to_path(port, "/send-draft", {"reply_mode": "reply"}).status
                == 400
            )
            # Unknown message_id → 404.
            assert (
                _post_to_path(
                    port,
                    "/send-draft",
                    {"message_id": "nope", "reply_mode": "reply"},
                ).status
                == 404
            )
            # Empty draft_text → 400.
            assert (
                _post_to_path(
                    port,
                    "/send-draft",
                    {"message_id": "send-empty-mid", "reply_mode": "reply"},
                ).status
                == 400
            )
            # Invalid reply_mode → 400.
            assert (
                _post_to_path(
                    port,
                    "/send-draft",
                    {"message_id": "send-valid-mid", "reply_mode": "bogus"},
                ).status
                == 400
            )

            # No mail sent, no archive move performed.
            smtp_cls.return_value.__enter__.return_value.send.assert_not_called()
            imap_move = imap_cls.return_value.__enter__.return_value.move_message
            imap_move.assert_not_called()

        # Records still present.
        assert urlopen(f"http://127.0.0.1:{port}/email/send-valid-mid").status == 200
    finally:
        server.shutdown()


def test_send_draft_missing_smtp_config_returns_400(single_db: str) -> None:
    """POST /send-draft with no SMTP config → 400, nothing sent."""
    _seed_draft_record(
        single_db,
        "send-nosmtp-mid",
        sender="alice@other.com",
        subject="Subject",
        draft_text="A draft.",
    )
    # No mail_config bound → SMTP not configured.
    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(
            port,
            "/send-draft",
            {"message_id": "send-nosmtp-mid", "reply_mode": "reply"},
        )
        assert resp.status == 400
        # Record untouched.
        assert urlopen(f"http://127.0.0.1:{port}/email/send-nosmtp-mid").status == 200
    finally:
        server.shutdown()


def test_send_draft_buttons_rendered_only_when_draft_ready(single_db: str) -> None:
    """The detail page renders both /send-draft forms only for DRAFT_READY."""
    _seed_draft_record(
        single_db,
        "ui-ready-mid",
        sender="alice@other.com",
        subject="Ready",
        draft_text="A draft.",
        action="DRAFT_READY",
    )
    _seed_draft_record(
        single_db,
        "ui-answer-mid",
        sender="bob@other.com",
        subject="Answer",
        draft_text="",
        action="TO_ANSWER",
    )

    server, port = _start_test_server(single_db)
    try:
        ready = (
            urlopen(f"http://127.0.0.1:{port}/email/ui-ready-mid")
            .read()
            .decode("utf-8")
        )
        assert 'action="/send-draft"' in ready
        assert 'name="reply_mode" value="reply"' in ready
        assert 'name="reply_mode" value="reply_all"' in ready
        assert 'name="reply_mode" value="forward"' in ready

        answer = (
            urlopen(f"http://127.0.0.1:{port}/email/ui-answer-mid")
            .read()
            .decode("utf-8")
        )
        assert 'action="/send-draft"' not in answer
    finally:
        server.shutdown()
