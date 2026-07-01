"""Unit tests for ``_BoardActionMixin`` methods.

Drives the mixin directly against a mock handler *self*, isolating the
logic from the HTTP transport and covering branches that integration
tests miss (stale-UID cross-folder
healing, path-traversal safety, no-strip parsing, etc.).
"""

from __future__ import annotations

import sqlite3
from typing import Any, Callable
from unittest import mock

import pytest
from tests.server.conftest import (
    _populate_db,
    _seed_archive_override,
)

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import get_record_by_message_id, init_db
from robotsix_auto_mail.imap import ImapError
from robotsix_auto_mail.server._action_mixin import _BoardActionMixin
from robotsix_auto_mail.triage import TO_ARCHIVE


@pytest.fixture(autouse=True)
def record_user_action_mock() -> Any:
    """Patch ``record_user_action`` so no background flash-LLM thread runs.

    The move/archive handlers call ``record_user_action`` to update the
    triage-rules file via a background daemon thread.  Patch it out so tests
    stay deterministic and never touch the network.
    """
    with mock.patch(
        "robotsix_auto_mail.server._action_mixin.record_user_action"
    ) as patched:
        yield patched


# ---------------------------------------------------------------------------
# Fake handler factory
# ---------------------------------------------------------------------------


class _FakeHandler(_BoardActionMixin):
    """Concrete handler that wires the ``BoardHandlerProtocol`` attributes
    to MagicMock defaults so mixin methods can be called directly."""

    def __init__(
        self,
        db_path: str,
        mail_config: MailConfig | None = None,
    ) -> None:
        self.db_path = db_path
        self.mail_config = mail_config
        self.headers = mock.MagicMock()
        self.rfile = mock.MagicMock()
        self._send_response = mock.MagicMock()
        self._redirect = mock.MagicMock()
        self._not_found = mock.MagicMock()
        self._bad_request = mock.MagicMock()


# ---------------------------------------------------------------------------
# Synchronous fake ``Thread`` so background daemon code is deterministic.
# ---------------------------------------------------------------------------


class _SyncThread:
    """Drop-in replacement for ``threading.Thread`` that runs *target*
    synchronously inside ``start()``."""

    def __init__(
        self,
        group: object = None,
        target: Callable[..., None] | None = None,
        name: str | None = None,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        *,
        daemon: bool | None = None,
    ) -> None:
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self) -> None:
        if self._target is not None:
            self._target(*self._args, **self._kwargs)


# ===================================================================
# _parse_request_body
# ===================================================================


class TestParseRequestBody:
    def test_strips_fields_by_default(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        handler.headers.get.return_value = 50
        handler.rfile.read.return_value = b"field1=++hello++&field2=++world++"

        result = handler._parse_request_body("field1", "field2")
        assert result == {"field1": "hello", "field2": "world"}

    def test_no_strip_preserves_whitespace(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        handler.headers.get.return_value = 60
        handler.rfile.read.return_value = (
            b"notes=++leading+trailing++&other=++trimmed++"
        )

        result = handler._parse_request_body(
            "notes", "other", no_strip=frozenset({"notes"})
        )
        # notes: spaces preserved (the '+' signs decode to spaces in
        # URL-encoded form, and parse_qs doesn't strip).
        assert result["notes"].startswith("  ")
        assert result["notes"].endswith("  ")
        assert "leading trailing" in result["notes"]
        # other: stripped by default.
        assert result["other"] == "trimmed"

    def test_missing_fields_default_to_empty_string(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        handler.headers.get.return_value = 12
        handler.rfile.read.return_value = b"field1=hello"

        result = handler._parse_request_body("field1", "field2")
        assert result == {"field1": "hello", "field2": ""}

    def test_content_length_honored(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        handler.headers.get.return_value = 7
        handler.rfile.read.return_value = b"field1=hello&field2=world"

        result = handler._parse_request_body("field1")
        # Only 7 bytes read: "field1=" — but parse_qs handles truncated input.
        assert "field1" in result

    def test_single_field_value_takes_first(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        handler.headers.get.return_value = 30
        handler.rfile.read.return_value = b"field1=first&field1=second"

        result = handler._parse_request_body("field1")
        assert result == {"field1": "first"}

    def test_empty_body_yields_empty_strings(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        handler.headers.get.return_value = 0
        handler.rfile.read.return_value = b""

        result = handler._parse_request_body("field1", "field2")
        assert result == {"field1": "", "field2": ""}


# ===================================================================
# _handle_post_action
# ===================================================================


class TestHandlePostAction:
    def test_missing_message_id_returns_400(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        handler.headers.get.return_value = 0
        handler.rfile.read.return_value = b""
        action = mock.MagicMock()

        handler._handle_post_action("message_id", "redirect_to", action=action)
        handler._bad_request.assert_called_once_with("Missing message_id")
        action.assert_not_called()

    def test_record_not_found_returns_404(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        handler.headers.get.return_value = 50
        handler.rfile.read.return_value = b"message_id=does-not-exist&redirect_to=/foo"
        action = mock.MagicMock()

        handler._handle_post_action("message_id", "redirect_to", action=action)
        handler._not_found.assert_called_once()
        action.assert_not_called()

    def test_action_returns_false_skips_redirect(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "act-false",
                    "sender": "x@x.com",
                    "subject": "Test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 60
        handler.rfile.read.return_value = b"message_id=act-false&redirect_to=/safe"
        action = mock.MagicMock(return_value=False)

        handler._handle_post_action("message_id", "redirect_to", action=action)
        action.assert_called_once()
        handler._redirect.assert_not_called()

    def test_safe_redirect_to_used(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "safe-redir",
                    "sender": "x@x.com",
                    "subject": "Test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 70
        handler.rfile.read.return_value = (
            b"message_id=safe-redir&redirect_to=/some/board?col=1"
        )
        action = mock.MagicMock(return_value=True)

        handler._handle_post_action("message_id", "redirect_to", action=action)
        handler._redirect.assert_called_once_with("/some/board?col=1", code=302)

    def test_unsafe_redirect_to_falls_back_to_board(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "unsafe-redir",
                    "sender": "x@x.com",
                    "subject": "Test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 85
        handler.rfile.read.return_value = (
            b"message_id=unsafe-redir&redirect_to=//evil.com/phish"
        )
        action = mock.MagicMock(return_value=True)

        handler._handle_post_action("message_id", "redirect_to", action=action)
        handler._redirect.assert_called_once_with("/board", code=302)

    def test_empty_redirect_to_falls_back_to_board(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "empty-redir",
                    "sender": "x@x.com",
                    "subject": "Test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 60
        handler.rfile.read.return_value = b"message_id=empty-redir&redirect_to="
        action = mock.MagicMock(return_value=True)

        handler._handle_post_action("message_id", "redirect_to", action=action)
        handler._redirect.assert_called_once_with("/board", code=302)


# ===================================================================
# _launch_background_worker
# ===================================================================


class TestLaunchBackgroundWorker:
    def test_free_watermark_spawns_and_redirects(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        target = mock.MagicMock()

        with mock.patch(
            "robotsix_auto_mail.server._action_mixin.threading.Thread",
            _SyncThread,
        ):
            result = handler._launch_background_worker(
                "wm:test", target=target, args=(42,)
            )

        assert result is True
        target.assert_called_once_with(42)
        handler._redirect.assert_called_once_with("/board", code=302)

    def test_running_watermark_returns_false_no_spawn(self, tmp_db_path: str) -> None:
        # Seed the watermark as "running".
        conn = init_db(tmp_db_path, skip_migrations=True)
        from robotsix_auto_mail.db import set_watermark

        set_watermark(conn, "wm:locked", "running")
        conn.close()

        handler = _FakeHandler(tmp_db_path)
        target = mock.MagicMock()

        result = handler._launch_background_worker("wm:locked", target=target)
        assert result is False
        target.assert_not_called()
        handler._redirect.assert_called_once_with("/board", code=302)

    def test_precheck_false_returns_false_no_spawn(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        target = mock.MagicMock()
        precheck = mock.MagicMock(return_value=False)

        result = handler._launch_background_worker(
            "wm:precheck", target=target, precheck=precheck
        )
        assert result is False
        target.assert_not_called()
        handler._redirect.assert_called_once_with("/board", code=302)

    def test_redirect_false_does_not_redirect(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        target = mock.MagicMock()

        with mock.patch(
            "robotsix_auto_mail.server._action_mixin.threading.Thread",
            _SyncThread,
        ):
            result = handler._launch_background_worker(
                "wm:noredir", target=target, redirect=False
            )

        assert result is True
        target.assert_called_once()
        handler._redirect.assert_not_called()

    def test_no_target_still_acquires_watermark(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)

        result = handler._launch_background_worker(
            "wm:notarget", target=None, redirect=False
        )
        assert result is True
        handler._redirect.assert_not_called()

    def test_custom_running_check(self, tmp_db_path: str) -> None:
        """A custom ``running_check`` that considers any non-None value
        as running prevents spawn."""
        conn = init_db(tmp_db_path, skip_migrations=True)
        from robotsix_auto_mail.db import set_watermark

        set_watermark(conn, "wm:cust", "busy")
        conn.close()

        handler = _FakeHandler(tmp_db_path)
        target = mock.MagicMock()

        def _any_non_none(v: str | None) -> bool:
            return v is not None

        result = handler._launch_background_worker(
            "wm:cust", target=target, running_check=_any_non_none
        )
        assert result is False
        target.assert_not_called()


# ===================================================================
# _handle_move
# ===================================================================


class TestHandleMove:
    def test_missing_triage_action_returns_400(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "mov-me",
                    "sender": "x@x.com",
                    "subject": "Move",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 50
        handler.rfile.read.return_value = (
            b"message_id=mov-me&triage_action=&redirect_to=/board"
        )

        handler._handle_move()
        handler._bad_request.assert_called_once()
        assert "Missing triage_action" in str(handler._bad_request.call_args[0][0])

    def test_invalid_triage_action_returns_400(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "mov-me",
                    "sender": "x@x.com",
                    "subject": "Move",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 60
        handler.rfile.read.return_value = (
            b"message_id=mov-me&triage_action=NOT_AN_ACTION&redirect_to=/board"
        )

        handler._handle_move()
        handler._bad_request.assert_called_once()
        assert "Invalid triage action" in str(handler._bad_request.call_args[0][0])

    def test_valid_move_persists_decision(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "valid-move",
                    "sender": "x@x.com",
                    "subject": "Test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 80
        handler.rfile.read.return_value = (
            b"message_id=valid-move&triage_action=TO_DELETE&redirect_to=/board"
        )

        handler._handle_move()

        # Verify DB state.
        conn = init_db(single_db)
        try:
            from robotsix_auto_mail.triage import get_triage_decision

            decision = get_triage_decision(conn, "valid-move")
            assert decision is not None
            assert decision.action == "TO_DELETE"
            assert decision.source == "user"
        finally:
            conn.close()

    def test_move_integrity_error_returns_400(self, single_db: str) -> None:
        """When ``set_triage_decision`` raises ``IntegrityError``,
        ``_bad_request`` is called and the redirect is skipped."""
        _populate_db(
            single_db,
            [
                {
                    "message_id": "integ2",
                    "sender": "x@x.com",
                    "subject": "Test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 70
        handler.rfile.read.return_value = (
            b"message_id=integ2&triage_action=TO_DELETE&redirect_to=/board"
        )

        with mock.patch(
            "robotsix_auto_mail.server._action_mixin.set_triage_decision",
            side_effect=sqlite3.IntegrityError("CHECK constraint failed"),
        ):
            handler._handle_move()

        handler._bad_request.assert_called_once()
        assert "Could not move" in str(handler._bad_request.call_args[0][0])

    # -- TO_ARCHIVE --------------------------------------------------------

    def test_to_archive_invokes_llm_proposal(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-llm",
                    "sender": "x@x.com",
                    "subject": "Archive me",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            llm_api_key="sk-test",
            llm_provider_model="openrouter-deepseek",
        )
        handler = _FakeHandler(single_db, mail_config=mail_config)
        handler.headers.get.return_value = 90
        handler.rfile.read.return_value = (
            b"message_id=arch-llm&triage_action=TO_ARCHIVE&redirect_to=/board"
        )

        with mock.patch(
            "robotsix_auto_mail.server._action_mixin.propose_archive_subfolder_llm"
        ) as mock_propose:
            handler._handle_move()

        mock_propose.assert_called_once()
        # Verify the call arguments.
        call_args = mock_propose.call_args
        assert call_args[0][1].message_id == "arch-llm"  # record (pos 1)
        assert call_args[0][2] == "sk-test"  # api_key (pos 2)
        assert call_args[1] == {
            "provider_model": "openrouter-deepseek",
            "rules": "",
        }  # provider_model + rules kwargs

    def test_to_archive_llm_exception_is_swallowed(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-llm-err",
                    "sender": "x@x.com",
                    "subject": "Archive LLM err",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            llm_api_key="sk-test",
        )
        handler = _FakeHandler(single_db, mail_config=mail_config)
        handler.headers.get.return_value = 100
        handler.rfile.read.return_value = (
            b"message_id=arch-llm-err&triage_action=TO_ARCHIVE&redirect_to=/board"
        )

        with mock.patch(
            "robotsix_auto_mail.server._action_mixin.propose_archive_subfolder_llm",
            side_effect=RuntimeError("LLM timeout"),
        ):
            # Should not raise — the exception is swallowed.
            handler._handle_move()

        # The move still succeeds (redirect happens).
        handler._redirect.assert_called()

    def test_to_archive_no_mail_config_skips_llm(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-no-cfg",
                    "sender": "x@x.com",
                    "subject": "No config",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db, mail_config=None)
        handler.headers.get.return_value = 80
        handler.rfile.read.return_value = (
            b"message_id=arch-no-cfg&triage_action=TO_ARCHIVE&redirect_to=/board"
        )

        with mock.patch(
            "robotsix_auto_mail.server._action_mixin.propose_archive_subfolder_llm"
        ) as mock_propose:
            handler._handle_move()

        mock_propose.assert_not_called()


# ===================================================================
# _handle_delete
# ===================================================================


class TestHandleDelete:
    def test_no_imap_config_deletes_locally(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "no-imap-del",
                    "sender": "x@x.com",
                    "subject": "No IMAP",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db, mail_config=None)
        handler.headers.get.return_value = 80
        handler.rfile.read.return_value = b"message_id=no-imap-del&redirect_to=/board"

        handler._handle_delete()

        conn = init_db(single_db)
        try:
            assert get_record_by_message_id(conn, "no-imap-del") is None
        finally:
            conn.close()

    def test_no_imap_uid_deletes_locally(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "no-uid-del",
                    "sender": "x@x.com",
                    "subject": "No UID",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(single_db, mail_config=mail_config)
        handler.headers.get.return_value = 80
        handler.rfile.read.return_value = b"message_id=no-uid-del&redirect_to=/board"

        handler._handle_delete()

        conn = init_db(single_db)
        try:
            assert get_record_by_message_id(conn, "no-uid-del") is None
        finally:
            conn.close()

    def test_happy_imap_delete(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "happy-imap-del",
                    "sender": "x@x.com",
                    "subject": "Happy IMAP",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        conn = init_db(single_db)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (55, "happy-imap-del"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(single_db, mail_config=mail_config)
        handler.headers.get.return_value = 90
        handler.rfile.read.return_value = (
            b"message_id=happy-imap-del&redirect_to=/board"
        )

        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.search_uids.return_value = [55]

            handler._handle_delete()

        mock_client.delete_message.assert_called_once_with(55)
        # Local record removed.
        conn2 = init_db(single_db)
        try:
            assert get_record_by_message_id(conn2, "happy-imap-del") is None
        finally:
            conn2.close()

    def test_imap_not_found_cross_folder_heal(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "cf-heal-del",
                    "sender": "x@x.com",
                    "subject": "Cross-folder heal",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        conn = init_db(single_db)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ?, source_folder = ? "
                "WHERE message_id = ?",
                (42, "INBOX", "cf-heal-del"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(single_db, mail_config=mail_config)
        handler.headers.get.return_value = 90
        handler.rfile.read.return_value = b"message_id=cf-heal-del&redirect_to=/board"

        with (
            mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
            mock.patch("robotsix_auto_mail.imap.cross_folder_resolve") as mock_cross,
        ):
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.search_uids.return_value = []
            mock_cross.return_value = ("Projects", 99)

            handler._handle_delete()

        # The second client should have called delete_message with the
        # healed UID.
        mock_client.delete_message.assert_called_once_with(99)

        # Local record removed.
        conn2 = init_db(single_db)
        try:
            assert get_record_by_message_id(conn2, "cf-heal-del") is None
        finally:
            conn2.close()

    def test_imap_not_found_cross_folder_heal_failure_502(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "cf-heal-fail",
                    "sender": "x@x.com",
                    "subject": "Heal fail",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        conn = init_db(single_db)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (42, "cf-heal-fail"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(single_db, mail_config=mail_config)
        handler.headers.get.return_value = 95
        handler.rfile.read.return_value = b"message_id=cf-heal-fail&redirect_to=/board"

        with (
            mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
            mock.patch("robotsix_auto_mail.imap.cross_folder_resolve") as mock_cross,
        ):
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.search_uids.return_value = []
            mock_cross.side_effect = ImapError("connection lost")

            handler._handle_delete()

        handler._send_response.assert_called_once()
        call_args = handler._send_response.call_args
        assert call_args[1]["status"] == 502

        # Local record preserved.
        conn2 = init_db(single_db)
        try:
            assert get_record_by_message_id(conn2, "cf-heal-fail") is not None
        finally:
            conn2.close()

    def test_imap_error_returns_502(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "imap-err-del",
                    "sender": "x@x.com",
                    "subject": "IMAP error",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        conn = init_db(single_db)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (10, "imap-err-del"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(single_db, mail_config=mail_config)
        handler.headers.get.return_value = 85
        handler.rfile.read.return_value = b"message_id=imap-err-del&redirect_to=/board"

        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            # Make the context manager itself raise ImapError on enter.
            mock_cls.side_effect = ImapError("connection refused")

            handler._handle_delete()

        handler._send_response.assert_called_once()
        call_args = handler._send_response.call_args
        assert call_args[1]["status"] == 502

        # Local record preserved.
        conn2 = init_db(single_db)
        try:
            assert get_record_by_message_id(conn2, "imap-err-del") is not None
        finally:
            conn2.close()


# ===================================================================
# _imap_archive_move
# ===================================================================


class TestImapArchiveMove:
    def test_value_error_when_dest_escapes_root(self) -> None:
        """When ``_archive_dest_folder`` returns ``None``, ValueError
        is raised."""
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(":memory:", mail_config=mail_config)

        with (
            mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
            mock.patch(
                "robotsix_auto_mail.server.adapters._archive_dest_folder",
                return_value=None,  # path traversal detected
            ),
        ):
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

            with pytest.raises(ValueError, match="escapes archive root"):
                handler._imap_archive_move(
                    mail_config,
                    imap_uid=1,
                    effective_root="my-archive",
                    subfolder="..",
                )

    def test_folder_hierarchy_created_level_by_level(self) -> None:
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(":memory:", mail_config=mail_config)

        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]
            mock_client.search_uids.return_value = [7]

            handler._imap_archive_move(
                mail_config,
                imap_uid=7,
                effective_root="my-archive",
                subfolder="Lists/new-list",
                source_folder="INBOX",
                message_id="hier",
            )

        expected_calls = [
            mock.call("my-archive"),
            mock.call("my-archive/Lists"),
            mock.call("my-archive/Lists/new-list"),
        ]
        assert mock_client.create_folder.call_args_list == expected_calls
        mock_client.move_message.assert_called_once_with(7, "my-archive/Lists/new-list")

    def test_no_subfolder_creates_root_only(self) -> None:
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(":memory:", mail_config=mail_config)

        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]
            mock_client.search_uids.return_value = [3]

            handler._imap_archive_move(
                mail_config,
                imap_uid=3,
                effective_root="my-archive",
                subfolder=None,
                source_folder="INBOX",
                message_id="rootonly",
            )

        mock_client.create_folder.assert_called_once_with("my-archive")
        mock_client.move_message.assert_called_once_with(3, "my-archive")

    def test_different_delimiter_respected(self) -> None:
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(":memory:", mail_config=mail_config)

        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [mock.Mock(delimiter=".")]
            mock_client.search_uids.return_value = [23]

            handler._imap_archive_move(
                mail_config,
                imap_uid=23,
                effective_root="my-archive",
                subfolder="Lists/new-list",
                source_folder="INBOX",
                message_id="dot-delim",
            )

        expected_calls = [
            mock.call("my-archive"),
            mock.call("my-archive.Lists"),
            mock.call("my-archive.Lists.new-list"),
        ]
        assert mock_client.create_folder.call_args_list == expected_calls
        mock_client.move_message.assert_called_once_with(
            23, "my-archive.Lists.new-list"
        )

    def test_resolve_uid_fallback_uses_message_id(self) -> None:
        """When the stored UID is stale, the Message-ID fallback finds
        the correct UID."""
        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )
        handler = _FakeHandler(":memory:", mail_config=mail_config)

        with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

            call_count = [0]

            def _search_uids(criteria: str) -> list[int]:
                call_count[0] += 1
                if "UID 42" in criteria:
                    return []  # stale
                if "fallback-test" in criteria:
                    return [77]  # found via Message-ID
                return [42]

            mock_client.search_uids.side_effect = _search_uids

            handler._imap_archive_move(
                mail_config,
                imap_uid=42,
                effective_root="my-archive",
                subfolder=None,
                source_folder="INBOX",
                message_id="fallback-test",
            )

        # move_message called with the resolved UID (77), not the stale one.
        mock_client.move_message.assert_called_once_with(77, "my-archive")


# ===================================================================
# _archive_and_delete
# ===================================================================


class TestArchiveAndDelete:
    def test_happy_path_deletes_local_after_imap_move(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-rec",
                    "sender": "x@x.com",
                    "subject": "Archive me",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        conn = init_db(single_db)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (7, "arch-rec"),
            )
            conn.commit()
            record = get_record_by_message_id(conn, "arch-rec")
            assert record is not None
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="my-archive",
        )
        handler = _FakeHandler(single_db, mail_config=mail_config)
        # Re-open: the mixin methods use their own connections for some
        # operations but _archive_and_delete receives conn as an arg.
        conn2 = init_db(single_db)
        try:
            record2 = get_record_by_message_id(conn2, "arch-rec")
            assert record2 is not None

            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]
                mock_client.search_uids.return_value = [7]

                result = handler._archive_and_delete(conn2, record2)

            assert result is True
        finally:
            conn2.close()

        # Local record deleted.
        conn3 = init_db(single_db)
        try:
            assert get_record_by_message_id(conn3, "arch-rec") is None
        finally:
            conn3.close()

    def test_value_error_returns_400_preserves_record(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-rec",
                    "sender": "x@x.com",
                    "subject": "Archive me",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        conn = init_db(single_db)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (7, "arch-rec"),
            )
            conn.commit()
            record = get_record_by_message_id(conn, "arch-rec")
            assert record is not None

            mail_config = MailConfig(
                imap_host="imap.example.com",
                smtp_host="smtp.example.com",
                username="test",
                password="test",
                archive_root="my-archive",
            )
            handler = _FakeHandler(single_db, mail_config=mail_config)

            with (
                mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
                mock.patch(
                    "robotsix_auto_mail.server.adapters._archive_dest_folder",
                    return_value=None,
                ),
            ):
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

                result = handler._archive_and_delete(conn, record)

            assert result is False
            handler._bad_request.assert_called_once()
        finally:
            conn.close()

        # Local record preserved.
        conn2 = init_db(single_db)
        try:
            assert get_record_by_message_id(conn2, "arch-rec") is not None
        finally:
            conn2.close()

    def test_imap_error_returns_502_preserves_record(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-rec",
                    "sender": "x@x.com",
                    "subject": "Archive me",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        conn = init_db(single_db)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (7, "arch-rec"),
            )
            conn.commit()
            record = get_record_by_message_id(conn, "arch-rec")
            assert record is not None

            mail_config = MailConfig(
                imap_host="imap.example.com",
                smtp_host="smtp.example.com",
                username="test",
                password="test",
                archive_root="my-archive",
            )
            handler = _FakeHandler(single_db, mail_config=mail_config)

            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_cls.side_effect = ImapError("connection refused")

                result = handler._archive_and_delete(conn, record)

            assert result is False
            handler._send_response.assert_called_once()
            assert handler._send_response.call_args[1]["status"] == 502
        finally:
            conn.close()

        # Local record preserved.
        conn2 = init_db(single_db)
        try:
            assert get_record_by_message_id(conn2, "arch-rec") is not None
        finally:
            conn2.close()

    def test_stale_uid_cross_folder_heal_and_delete(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-rec",
                    "sender": "x@x.com",
                    "subject": "Archive me",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        conn = init_db(single_db)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ?, source_folder = ? "
                "WHERE message_id = ?",
                (42, "INBOX", "arch-rec"),
            )
            conn.commit()
            record = get_record_by_message_id(conn, "arch-rec")
            assert record is not None

            mail_config = MailConfig(
                imap_host="imap.example.com",
                smtp_host="smtp.example.com",
                username="test",
                password="test",
                archive_root="my-archive",
            )
            handler = _FakeHandler(single_db, mail_config=mail_config)

            with (
                mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
                mock.patch(
                    "robotsix_auto_mail.imap.cross_folder_resolve"
                ) as mock_cross,
            ):
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.search_uids.return_value = []
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]
                mock_cross.return_value = ("Projects", 99)

                result = handler._archive_and_delete(conn, record)

            assert result is True
            # Verify that the healed UID was moved.
            mock_client.move_message.assert_called_once()
            move_uid = mock_client.move_message.call_args[0][0]
            assert move_uid == 99
        finally:
            conn.close()

        # Local record deleted.
        conn2 = init_db(single_db)
        try:
            assert get_record_by_message_id(conn2, "arch-rec") is None
        finally:
            conn2.close()

    def test_records_user_action_before_delete(
        self, single_db: str, record_user_action_mock: Any
    ) -> None:
        """When a subfolder is chosen, ``record_user_action`` is called with
        that subfolder before the local row is deleted."""
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-rec",
                    "sender": "x@x.com",
                    "subject": "Archive me",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_archive_override(single_db, "arch-rec", "Lists/new-list")
        conn = init_db(single_db)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (7, "arch-rec"),
            )
            conn.commit()
            record = get_record_by_message_id(conn, "arch-rec")
            assert record is not None

            mail_config = MailConfig(
                imap_host="imap.example.com",
                smtp_host="smtp.example.com",
                username="test",
                password="test",
                archive_root="my-archive",
            )
            handler = _FakeHandler(single_db, mail_config=mail_config)

            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]
                mock_client.search_uids.return_value = [7]

                result = handler._archive_and_delete(conn, record)

            assert result is True
            # record_user_action should have been called with the record,
            # the TO_ARCHIVE action, and the chosen subfolder.
            mock_record = record_user_action_mock
            mock_record.assert_called_once()
            call_args = mock_record.call_args
            assert call_args[0][0].message_id == "arch-rec"
            assert call_args[0][1] == TO_ARCHIVE
            assert call_args[1]["subfolder"] == "Lists/new-list"
        finally:
            conn.close()

    def test_no_imap_config_local_delete_only(self, single_db: str) -> None:
        """When mail_config is None, only local deletion happens."""
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-rec",
                    "sender": "x@x.com",
                    "subject": "Archive me",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        conn = init_db(single_db)
        try:
            record = get_record_by_message_id(conn, "arch-rec")
            assert record is not None

            handler = _FakeHandler(single_db, mail_config=None)
            result = handler._archive_and_delete(conn, record)

            assert result is True
        finally:
            conn.close()

        conn2 = init_db(single_db)
        try:
            assert get_record_by_message_id(conn2, "arch-rec") is None
        finally:
            conn2.close()


# ===================================================================
# _handle_archive
# ===================================================================


class TestHandleArchive:
    def test_delegates_to_archive_and_delete_and_redirects(
        self, single_db: str
    ) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "arch-wrap",
                    "sender": "x@x.com",
                    "subject": "Archive wrap",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db, mail_config=None)
        handler.headers.get.return_value = 70
        handler.rfile.read.return_value = b"message_id=arch-wrap&redirect_to=/board"

        handler._handle_archive()

        # Should redirect (success path) and delete the local record.
        handler._redirect.assert_called_once_with("/board", code=302)
        conn = init_db(single_db)
        try:
            assert get_record_by_message_id(conn, "arch-wrap") is None
        finally:
            conn.close()


# ===================================================================
# _handle_save_notes
# ===================================================================


class TestHandleSaveNotes:
    def test_notes_not_stripped(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "notes-test",
                    "sender": "x@x.com",
                    "subject": "Notes",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 80
        # URL-encoded: spaces become '+', %20, or actual spaces after
        # decoding.  parse_qs doesn't strip.  We include leading/trailing
        # spaces in the encoded form.
        handler.rfile.read.return_value = (
            b"message_id=notes-test&redirect_to=/board&notes=+++preserve+spaces+++"
        )

        handler._handle_save_notes()

        conn = init_db(single_db)
        try:
            record = get_record_by_message_id(conn, "notes-test")
            assert record is not None
            # The notes field should have leading/trailing spaces preserved.
            assert record.notes.startswith("   ")
            assert record.notes.endswith("   ")
            assert "preserve spaces" in record.notes
        finally:
            conn.close()

    def test_notes_persisted_to_db(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "notes-persist",
                    "sender": "x@x.com",
                    "subject": "Notes persist",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 90
        handler.rfile.read.return_value = (
            b"message_id=notes-persist&redirect_to=/board&notes=Hello+World"
        )

        handler._handle_save_notes()

        conn = init_db(single_db)
        try:
            record = get_record_by_message_id(conn, "notes-persist")
            assert record is not None
            assert record.notes == "Hello World"
        finally:
            conn.close()

    def test_missing_message_id_returns_400(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        handler.headers.get.return_value = 40
        handler.rfile.read.return_value = b"notes=some+notes&redirect_to=/board"

        handler._handle_save_notes()
        handler._bad_request.assert_called_once_with("Missing message_id")
