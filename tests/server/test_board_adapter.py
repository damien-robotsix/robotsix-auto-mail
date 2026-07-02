"""Unit tests for :class:`MailBoardAdapter`.

These tests lock the adapter as the single source of truth for the base
board scaffold data (column order + labels, card title, triage badge,
timestamps, move endpoint).  ``server/__init__.py`` consumes these
protocol methods directly, so a regression that re-inlines the
recomputation would change one of these return values and be caught
here.
"""

from __future__ import annotations

from urllib.request import urlopen

from robotsix_board import RenderMode
from tests.conftest import _make_record
from tests.server.conftest import (
    _make_extra_html_adapter,
    _start_test_server,
)

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
        ("TO_CALENDAR", "To calendar"),
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


# ===========================================================================
# MailBoardAdapter tests
# ===========================================================================


def test_mailboardadapter_satisfies_protocol() -> None:
    from robotsix_board import BoardAdapter

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    assert isinstance(adapter, BoardAdapter)


def test_mailboardadapter_columns_returns_correct_pairs() -> None:

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    cols = adapter.columns()
    assert isinstance(cols, list)
    assert len(cols) > 0
    for action, label in cols:
        assert isinstance(action, str)
        assert isinstance(label, str)


def test_mailboardadapter_card_id() -> None:

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    record = _make_record(message_id="<id@test.com>")
    assert adapter.card_id(record) == "<id@test.com>"


def test_mailboardadapter_card_title() -> None:

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    record = _make_record(sender="alice@x.com", subject="Hello")
    assert adapter.card_title(record) == "alice@x.com: Hello"


def test_mailboardadapter_card_title_no_subject() -> None:

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    record = _make_record(sender="alice@x.com", subject="   ")
    assert adapter.card_title(record) == "alice@x.com: (no subject)"


def test_mailboardadapter_card_badges() -> None:

    adapter = MailBoardAdapter(
        triage_by_mid={"<test@example.com>": "INBOX"},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    record = _make_record()
    badges = adapter.card_badges(record)
    assert badges == ["Inbox"]


def test_mailboardadapter_archive_override_offers_folder_dropdown() -> None:
    """The override field is a free-text input backed by a folder datalist."""
    mid = "<test@example.com>"
    record = _make_record(message_id=mid)
    adapter = MailBoardAdapter(
        triage_by_mid={mid: "TO_ARCHIVE"},
        archive_subfolders={mid: "Compta"},
        folder_exists={},
        archive_root="robotsix-mail-archive",
        unsubscribe_suggestions={},
        record_notes={},
        column_records={"TO_ARCHIVE": [record]},
        archive_folders=["Billing", "LS2N"],
    )
    # The per-card override input still accepts free text but is wired to the
    # shared datalist dropdown.
    card = adapter.card_extra_html(record)
    assert 'name="subfolder"' in card
    assert 'list="archive-folders"' in card

    # The datalist (emitted once on the column) lists the managed structure
    # folders plus the destination currently proposed for this column.
    col = adapter.column_extra_html("TO_ARCHIVE")
    assert '<datalist id="archive-folders">' in col
    assert '<option value="Billing">' in col
    assert '<option value="LS2N">' in col
    assert '<option value="Compta">' in col


def test_mailboardadapter_aggregate_forms_redirect_to_all_mailboxes() -> None:
    """In the aggregate view, card actions post back to the All-mailboxes board.

    The per-card form still targets the card's own account DB
    (``?account=<id>``), but a hidden ``redirect_to`` returns the user to
    ``/board?account=__all__`` instead of switching to that single account.
    """
    mid = "<test@example.com>"
    adapter = MailBoardAdapter(
        triage_by_mid={mid: "TO_DELETE"},
        archive_subfolders={},
        folder_exists={},
        archive_root="robotsix-mail-archive",
        unsubscribe_suggestions={},
        record_notes={},
        record_accounts={mid: "work"},
        account_labels={"work": "Work"},
    )
    card = adapter.card_extra_html(_make_record(message_id=mid))
    assert 'name="redirect_to" value="/board?account=__all__"' in card
    assert "?account=work" in card  # write still routes to the card's account


def test_mailboardadapter_single_account_has_no_aggregate_redirect() -> None:
    """Single-account cards omit the aggregate redirect (the cookie persists)."""
    mid = "<test@example.com>"
    adapter = MailBoardAdapter(
        triage_by_mid={mid: "TO_DELETE"},
        archive_subfolders={},
        folder_exists={},
        archive_root="robotsix-mail-archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    card = adapter.card_extra_html(_make_record(message_id=mid))
    assert "redirect_to" not in card


def test_mailboardadapter_card_badges_unmatched_returns_inbox() -> None:

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    record = _make_record()
    badges = adapter.card_badges(record)
    assert badges == ["Inbox"]


def test_mailboardadapter_card_timestamps() -> None:

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    record = _make_record(date="2025-06-01T12:00:00")
    ts = adapter.card_timestamps(record)
    assert "date" in ts
    assert "2025-06-01" in ts["date"]


def test_mailboardadapter_move_endpoint() -> None:

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    record = _make_record()
    url, method = adapter.move_endpoint(record)
    assert url == "/move"
    assert method == "post"


def test_mailboardadapter_move_endpoint_template() -> None:

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    assert adapter.move_endpoint_template() == "/move/{card_id}/{target_status}"


def test_mailboardadapter_render_mode() -> None:
    from robotsix_board import RenderMode

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    assert adapter.render_mode() == RenderMode.SERVER_FRAGMENTS


# ===========================================================================
# Board HTML structure tests (HTTP-level)
# ===========================================================================


def test_column_extra_html_to_delete_wraps_buttons_banner_outside() -> None:
    """TO_DELETE wraps both forms in .column-extra-top, banner stays after it."""
    adapter = _make_extra_html_adapter()
    html_out = adapter.column_extra_html("TO_DELETE")
    open_idx = html_out.find('<div class="column-extra-top">')
    close_idx = html_out.find("</div>", open_idx)
    assert open_idx != -1
    assert close_idx != -1
    wrapper = html_out[open_idx : close_idx + len("</div>")]
    # Both action-button forms live inside the wrapper.
    assert 'class="delete-form"' in wrapper
    assert 'class="delete-btn"' in wrapper
    assert 'class="force-triage-form"' in wrapper
    assert 'class="force-triage-btn"' in wrapper
    # The unsubscribe banner sits AFTER the closing wrapper div.
    banner_idx = html_out.find('class="unsubscribe-banner"')
    assert banner_idx != -1
    assert banner_idx > close_idx


def test_column_extra_html_non_to_delete_has_force_triage_only() -> None:
    """A non-INBOX, non-TO_DELETE column wraps the force-triage form only."""
    adapter = _make_extra_html_adapter()
    html_out = adapter.column_extra_html("TO_ARCHIVE")
    assert '<div class="column-extra-top">' in html_out
    assert 'class="force-triage-form"' in html_out
    assert 'class="delete-form"' not in html_out


def test_served_css_orders_banner_above_column_extra_top_above_cards() -> None:
    """The served stylesheet sorts the unsubscribe banner to the column top."""
    server, port = _start_test_server(":memory:")
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/static/automail/board.css")
        body = resp.read().decode("utf-8")
        assert "column-extra-top" in body
        assert "order: 1" in body
        assert "board-column-cards" in body
        assert "order: 2" in body
        assert "unsubscribe-banner" in body
        assert "order: 0" in body
    finally:
        server.shutdown()


def test_unsubscribe_mailto_javascript_blocked() -> None:
    """``method=mailto`` with ``javascript:`` URL must NOT produce an anchor."""
    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="",
        unsubscribe_suggestions={
            "attacker@example.com": {
                "method": "mailto",
                "url": "javascript:alert(1)",
                "description": "Click here.",
            }
        },
        record_notes={},
        column_records={
            "TO_DELETE": [
                _make_record(
                    message_id="<x@example.com>", sender="attacker@example.com"
                )
            ],
        },
    )
    html_out = adapter.column_extra_html("TO_DELETE")
    # The banner should be rendered (the suggestion key matches the sender).
    assert "unsubscribe-banner" in html_out
    # But the javascript: URL must NOT produce an anchor.
    assert "<a href=" not in html_out


def test_unsubscribe_mailto_valid_url() -> None:
    """``method=mailto`` with a valid ``mailto:`` URL produces the anchor."""
    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="",
        unsubscribe_suggestions={
            "sender@example.com": {
                "method": "mailto",
                "url": "mailto:unsub@example.com",
                "description": "Reply to unsubscribe.",
            }
        },
        record_notes={},
        column_records={
            "TO_DELETE": [_make_record(message_id="<d1@example.com>")],
        },
    )
    html_out = adapter.column_extra_html("TO_DELETE")
    assert '<a href="mailto:unsub@example.com">Unsubscribe</a>' in html_out


def test_unsubscribe_header_mailto_still_works() -> None:
    """``method=header`` with a valid ``mailto:`` URL still produces the anchor."""
    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="",
        unsubscribe_suggestions={
            "sender@example.com": {
                "method": "header",
                "url": "mailto:unsub@example.com",
                "description": "List-Unsubscribe header.",
            }
        },
        record_notes={},
        column_records={
            "TO_DELETE": [_make_record(message_id="<d1@example.com>")],
        },
    )
    html_out = adapter.column_extra_html("TO_DELETE")
    assert '<a href="mailto:unsub@example.com">Unsubscribe</a>' in html_out
