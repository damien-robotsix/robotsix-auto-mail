"""Unit tests for ``src/robotsix_auto_mail/server/adapters.py``.

Covers the five utilities that lacked dedicated tests:
``_NonEmptyColumnsAdapter``, ``_run_triage_background``,
``_batch_op_running``, ``_archive_dest_folder`` and
``_collect_records_for_action``.
"""

from __future__ import annotations

import sqlite3
from unittest import mock

import pytest

from robotsix_auto_mail.server.adapters import (
    _archive_dest_folder,
    _batch_op_running,
    _collect_records_for_action,
    _NonEmptyColumnsAdapter,
    _run_triage_background,
)
from robotsix_auto_mail.triage import set_triage_decision

# ---------------------------------------------------------------------------
# _NonEmptyColumnsAdapter
# ---------------------------------------------------------------------------


class TestNonEmptyColumnsAdapter:
    def test_constructor_stores_adapter_and_status_keys(self) -> None:
        wrapped = mock.Mock()
        adapter = _NonEmptyColumnsAdapter(wrapped, ["INBOX", "TO_DELETE"])
        assert adapter._adapter is wrapped
        assert adapter._status_keys == ["INBOX", "TO_DELETE"]

    def test_columns_returns_only_status_keys_in_order(self) -> None:
        wrapped = mock.Mock()
        wrapped.columns.return_value = [
            ("INBOX", "Inbox"),
            ("HUMAN_TRIAGE", "Human triage"),
            ("TO_DELETE", "To delete"),
            ("TO_ARCHIVE", "To archive"),
        ]
        adapter = _NonEmptyColumnsAdapter(wrapped, ["TO_DELETE", "INBOX"])
        assert adapter.columns() == [
            ("TO_DELETE", "To delete"),
            ("INBOX", "Inbox"),
        ]

    def test_columns_handles_empty_status_keys(self) -> None:
        wrapped = mock.Mock()
        wrapped.columns.return_value = [("INBOX", "Inbox")]
        adapter = _NonEmptyColumnsAdapter(wrapped, [])
        assert adapter.columns() == []

    def test_getattr_delegates_to_wrapped_adapter(self) -> None:
        wrapped = mock.Mock()
        wrapped.card_title.return_value = "Test Title"
        adapter = _NonEmptyColumnsAdapter(wrapped, ["INBOX"])
        assert adapter.card_title(mock.Mock()) == "Test Title"
        wrapped.card_title.assert_called_once()

    def test_getattr_delegates_unknown_attr(self) -> None:
        wrapped = mock.Mock()
        wrapped.extra_prop = 42
        adapter = _NonEmptyColumnsAdapter(wrapped, ["INBOX"])
        assert adapter.extra_prop == 42


# ---------------------------------------------------------------------------
# _run_triage_background
# ---------------------------------------------------------------------------


class TestRunTriageBackground:
    def test_clears_watermark_on_success(self) -> None:
        mock_conn = mock.MagicMock(spec=sqlite3.Connection)
        with (
            mock.patch(
                "robotsix_auto_mail.db.init_db", return_value=mock_conn
            ) as mock_init_db,
            mock.patch("robotsix_auto_mail.db.set_watermark") as mock_set_watermark,
            mock.patch("robotsix_auto_mail.triage.run_triage_agent"),
        ):
            _run_triage_background("/fake/db.sqlite", user_email="a@b.com")

        mock_init_db.assert_called_once_with("/fake/db.sqlite", skip_migrations=True)
        mock_set_watermark.assert_called_once_with(
            mock_conn, "triage_run:state", "idle"
        )
        mock_conn.close.assert_called_once()

    def test_clears_watermark_when_agent_raises(self) -> None:
        mock_conn = mock.MagicMock(spec=sqlite3.Connection)
        with (
            mock.patch("robotsix_auto_mail.db.init_db", return_value=mock_conn),
            mock.patch("robotsix_auto_mail.db.set_watermark") as mock_set_watermark,
            mock.patch(
                "robotsix_auto_mail.triage.run_triage_agent",
                side_effect=RuntimeError("boom"),
            ),
        ):
            # Must not raise.
            _run_triage_background("/fake/db.sqlite")

        mock_set_watermark.assert_called_once_with(
            mock_conn, "triage_run:state", "idle"
        )
        mock_conn.close.assert_called_once()

    def test_clears_watermark_when_triage_import_fails(self) -> None:
        """When triage cannot be imported the watermark is still cleared."""
        mock_conn = mock.MagicMock(spec=sqlite3.Connection)
        with (
            mock.patch("robotsix_auto_mail.db.init_db", return_value=mock_conn),
            mock.patch("robotsix_auto_mail.db.set_watermark") as mock_set_watermark,
        ):
            # Make the triage module unimportable.
            with mock.patch.dict(
                "sys.modules",
                {"robotsix_auto_mail.triage": None},
            ):
                _run_triage_background("/fake/db.sqlite")

        mock_set_watermark.assert_called_once_with(
            mock_conn, "triage_run:state", "idle"
        )
        mock_conn.close.assert_called_once()

    def test_passes_user_email_to_agent(self) -> None:
        mock_conn = mock.MagicMock(spec=sqlite3.Connection)
        with (
            mock.patch("robotsix_auto_mail.db.init_db", return_value=mock_conn),
            mock.patch("robotsix_auto_mail.db.set_watermark"),
            mock.patch("robotsix_auto_mail.triage.run_triage_agent") as mock_run_triage,
        ):
            _run_triage_background("/fake/db.sqlite", user_email="x@y.com")

        mock_run_triage.assert_called_once_with(
            mock_conn, user_email="x@y.com", rules_path=None
        )

    def test_passes_none_user_email_by_default(self) -> None:
        mock_conn = mock.MagicMock(spec=sqlite3.Connection)
        with (
            mock.patch("robotsix_auto_mail.db.init_db", return_value=mock_conn),
            mock.patch("robotsix_auto_mail.db.set_watermark"),
            mock.patch("robotsix_auto_mail.triage.run_triage_agent") as mock_run_triage,
        ):
            _run_triage_background("/fake/db.sqlite")

        mock_run_triage.assert_called_once_with(
            mock_conn, user_email=None, rules_path=None
        )


# ---------------------------------------------------------------------------
# _batch_op_running
# ---------------------------------------------------------------------------


class TestBatchOpRunning:
    def test_none_is_not_running(self) -> None:
        assert _batch_op_running(None) is False

    def test_idle_is_not_running(self) -> None:
        assert _batch_op_running("idle") is False

    def test_running_is_running(self) -> None:
        assert _batch_op_running("running") is True

    def test_json_progress_payload_is_running(self) -> None:
        assert _batch_op_running('{"op":"delete","done":3,"total":10}') is True

    def test_empty_string_is_running(self) -> None:
        assert _batch_op_running("") is True


# ---------------------------------------------------------------------------
# _archive_dest_folder
# ---------------------------------------------------------------------------


class TestArchiveDestFolder:
    def test_no_subfolder_returns_effective_root(self) -> None:
        assert _archive_dest_folder("/Archive", None, ".") == "/Archive"

    def test_subfolder_with_slash_delimiter(self) -> None:
        dest = _archive_dest_folder("/Archive", "Lists/Dev", "/")
        assert dest == "/Archive/Lists/Dev"

    def test_subfolder_with_non_slash_delimiter(self) -> None:
        dest = _archive_dest_folder("[Gmail]/Archive", "Lists.Dev", ".")
        assert dest == "[Gmail]/Archive.Lists.Dev"

    def test_rejects_dot_dot_traversal(self) -> None:
        # "/" delimiter — "../" is a standalone ".." segment after split.
        assert _archive_dest_folder("/Archive", "../etc", "/") is None
        # "/" delimiter — subfolder that is exactly "..".
        assert _archive_dest_folder("/Archive", "..", "/") is None

    def test_rejects_destination_escaping_root(self) -> None:
        # The destination must start with root + delimiter.
        assert _archive_dest_folder("/Archive", "../../etc", "/") is None
        # A completely different prefix should be rejected.
        dest = _archive_dest_folder(
            "/Archive", "Other", "/"
        )  # "/Archive/Other" — starts with "/Archive/"
        assert dest == "/Archive/Other"
        # But "OtherArchive" as subfolder produces "/Archive/OtherArchive"
        # which does start with "/Archive/", so it passes.
        dest2 = _archive_dest_folder("/Archive", "OtherArchive", "/")
        assert dest2 == "/Archive/OtherArchive"

    def test_empty_subfolder_returns_root(self) -> None:
        assert _archive_dest_folder("/Root", "", "/") == "/Root"

    def test_slash_in_subfolder_translated_to_delimiter(self) -> None:
        dest = _archive_dest_folder("INBOX.Archive", "a/b/c", ".")
        assert dest == "INBOX.Archive.a.b.c"


# ---------------------------------------------------------------------------
# _collect_records_for_action
# ---------------------------------------------------------------------------


class TestCollectRecordsForAction:
    @pytest.fixture(autouse=True)
    def _setup_db(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def _insert_record(
        self, message_id: str, sender: str = "a@b.com", subject: str = "S"
    ) -> None:
        self.conn.execute(
            "INSERT INTO mail_records "
            "(message_id, sender, subject, date, recipients_json, "
            "body_plain, body_html, attachments_json, status) "
            "VALUES (?, ?, ?, '2025-01-01T00:00:00Z', '{}', '', '', '[]', 'to_read')",
            (message_id, sender, subject),
        )
        self.conn.commit()

    def test_returns_records_with_matching_action(self) -> None:
        self._insert_record("<a@test>")
        self._insert_record("<b@test>")
        self._insert_record("<c@test>")
        set_triage_decision(self.conn, "<a@test>", "TO_DELETE", source="agent")
        set_triage_decision(self.conn, "<b@test>", "TO_DELETE", source="agent")
        set_triage_decision(self.conn, "<c@test>", "TO_ARCHIVE", source="agent")

        results = _collect_records_for_action(self.conn, "TO_DELETE")
        mids = sorted(r.message_id for r in results)
        assert mids == ["<a@test>", "<b@test>"]

    def test_returns_empty_list_when_no_match(self) -> None:
        self._insert_record("<x@test>")
        set_triage_decision(self.conn, "<x@test>", "TO_ARCHIVE", source="agent")

        assert _collect_records_for_action(self.conn, "TO_DELETE") == []

    def test_returns_mailrecord_objects_with_correct_fields(self) -> None:
        self._insert_record("<z@test>", sender="z@b.com", subject="Zed")
        set_triage_decision(self.conn, "<z@test>", "TO_DELETE", source="agent")

        results = _collect_records_for_action(self.conn, "TO_DELETE")
        assert len(results) == 1
        record = results[0]
        assert record.message_id == "<z@test>"
        assert record.sender == "z@b.com"
        assert record.subject == "Zed"

    def test_returns_empty_when_no_triage_decisions_exist(self) -> None:
        assert _collect_records_for_action(self.conn, "TO_DELETE") == []

    def test_respects_action_filter(self) -> None:
        self._insert_record("<m1@test>")
        self._insert_record("<m2@test>")
        set_triage_decision(self.conn, "<m1@test>", "TO_ANSWER", source="agent")
        set_triage_decision(self.conn, "<m2@test>", "HUMAN_TRIAGE", source="user")

        assert len(_collect_records_for_action(self.conn, "TO_ANSWER")) == 1
        assert len(_collect_records_for_action(self.conn, "HUMAN_TRIAGE")) == 1
        assert len(_collect_records_for_action(self.conn, "TO_DELETE")) == 0
