"""Unit tests for :class:`MailBoardAdapter`.

These tests lock the adapter as the single source of truth for the base
board scaffold data (column order + labels, card title, triage badge,
timestamps, move endpoint).  ``server/__init__.py`` consumes these
protocol methods directly, so a regression that re-inlines the
recomputation would change one of these return values and be caught
here.
"""

from __future__ import annotations

from robotsix_board import RenderMode
from tests.conftest import _make_record

from robotsix_auto_mail.server.board_adapter import MailBoardAdapter


def _make_adapter(triage_by_mid: dict[str, str] | None = None) -> MailBoardAdapter:
    """Build a ``MailBoardAdapter`` with empty auto-mail-specific context."""
    return MailBoardAdapter(
        triage_by_mid=triage_by_mid or {},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )


def test_columns_order_and_labels() -> None:
    adapter = _make_adapter()
    assert adapter.columns() == [
        ("INBOX", "Inbox"),
        ("HUMAN_TRIAGE", "Human triage"),
        ("PENDING_ACTION", "Pending action"),
        ("TO_ARCHIVE", "To archive"),
        ("TO_DELETE", "To delete"),
        ("TO_ANSWER", "To answer"),
        ("DRAFT_READY", "Draft ready"),
    ]


def test_card_id() -> None:
    adapter = _make_adapter()
    record = _make_record(message_id="<id@test.com>")
    assert adapter.card_id(record) == "<id@test.com>"


def test_card_title() -> None:
    adapter = _make_adapter()
    record = _make_record(sender="alice@x.com", subject="Hello")
    assert adapter.card_title(record) == "alice@x.com: Hello"


def test_card_title_no_subject_fallback() -> None:
    adapter = _make_adapter()
    record = _make_record(sender="alice@x.com", subject="   ")
    assert adapter.card_title(record) == "alice@x.com: (no subject)"


def test_card_badges_uses_triage_decision() -> None:
    record = _make_record(message_id="<m@x.com>")
    adapter = _make_adapter(triage_by_mid={"<m@x.com>": "TO_DELETE"})
    assert adapter.card_badges(record) == ["To delete"]


def test_card_badges_absent_decision_falls_back_to_inbox() -> None:
    adapter = _make_adapter()
    record = _make_record()
    assert adapter.card_badges(record) == ["Inbox"]


def test_card_timestamps() -> None:
    adapter = _make_adapter()
    record = _make_record(date="2025-06-01T12:00:00Z")
    assert adapter.card_timestamps(record) == {"date": "2025-06-01 12:00"}


def test_move_endpoint() -> None:
    adapter = _make_adapter()
    record = _make_record()
    assert adapter.move_endpoint(record) == ("/move", "post")


def test_move_endpoint_template() -> None:
    adapter = _make_adapter()
    assert adapter.move_endpoint_template() == "/move/{card_id}/{target_status}"


def test_render_mode() -> None:
    adapter = _make_adapter()
    assert adapter.render_mode() == RenderMode.SERVER_FRAGMENTS
