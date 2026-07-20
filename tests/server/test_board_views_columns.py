"""Unit tests for ``_render_board_columns`` from
``robotsix_auto_mail.server.views.board``.
"""

from __future__ import annotations

import textwrap
from typing import Any
from unittest import mock

from robotsix_auto_mail.server.board_adapter import MailBoardAdapter
from tests.conftest import _make_record

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_minimal_adapter() -> MailBoardAdapter:
    """Build a minimal MailBoardAdapter with no records."""
    return MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
        column_records={},
    )


# ---------------------------------------------------------------------------
# _render_board_columns
# ---------------------------------------------------------------------------


def test_render_board_columns_empty_cards() -> None:
    """Returns the empty-board placeholder when *cards* is empty."""
    from robotsix_auto_mail.server.views.board import _render_board_columns

    adapter = _make_minimal_adapter()
    result = _render_board_columns(adapter, {})
    assert result == '<div class="empty-board">No mail yet.</div>'


def test_render_board_columns_strips_wrapper_and_drawer() -> None:
    """Strips the outer #board wrapper and #drawer from the library output."""
    from robotsix_auto_mail.server.views.board import _render_board_columns

    adapter = _make_minimal_adapter()
    card = _make_record(message_id="<mid@test>", subject="S1")
    cards: dict[str, list[Any]] = {"INBOX": [card]}

    # Simulate what render_board returns: an outer #board div, inner
    # columns, then the #drawer shell.
    fake_html = textwrap.dedent("""\
        <div id="board" class="board">
        <div class="board-column" data-status="INBOX">col content</div>
        <div id="drawer" class="drawer hidden"><div class="drawer-content"></div></div>
        </div>
    """).rstrip()

    with mock.patch(
        "robotsix_auto_mail.server.views.board.render_board",
        return_value=fake_html,
    ):
        result = _render_board_columns(adapter, cards)

    # The outer #board div and the #drawer are removed; only the inner
    # column markup remains.
    assert '<div id="board"' not in result
    assert '<div id="drawer"' not in result
    assert 'class="board-column"' in result
    assert "col content" in result


def test_render_board_columns_no_wrapper_in_output() -> None:
    """When render_board output lacks the expected wrapper prefix, the
    stripping logic still degrades gracefully (inner rfind strip)."""
    from robotsix_auto_mail.server.views.board import _render_board_columns

    adapter = _make_minimal_adapter()
    card = _make_record(message_id="<mid@test>", subject="S2")
    cards: dict[str, list[Any]] = {"INBOX": [card]}

    # Output that does NOT start with the expected wrapper tag but does
    # contain the drawer.
    fake_html = textwrap.dedent("""\
        <div class="board-column" data-status="INBOX">bare columns</div>
        <div id="drawer" class="drawer hidden"><div class="drawer-content"></div></div>
    """).rstrip()

    with mock.patch(
        "robotsix_auto_mail.server.views.board.render_board",
        return_value=fake_html,
    ):
        result = _render_board_columns(adapter, cards)

    assert "bare columns" in result
    assert '<div id="drawer"' not in result
