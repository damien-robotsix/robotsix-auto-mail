"""Unit tests for module-level board view functions from
``robotsix_auto_mail.server.views.board``.

Tests the 6 functions that currently lack direct unit coverage:
``_render_board_columns``, ``_batch_banner_html``,
``_gather_account_board_data``, ``_render_board_page_shell``,
``_build_board_html``, and ``_build_global_board_html``.
"""

from __future__ import annotations

import json
import textwrap
from typing import Any
from unittest import mock

from tests.conftest import _make_record
from tests.server.conftest import (
    _populate_db,
    _seed_archive_override,
    _seed_archive_structure,
    _seed_triage_decision,
)

from robotsix_auto_mail.config import (
    DEFAULT_ARCHIVE_ROOT,
    MailAccount,
    MailAccountsConfig,
)
from robotsix_auto_mail.db import init_db, set_watermark
from robotsix_auto_mail.server.board_adapter import MailBoardAdapter

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


# ---------------------------------------------------------------------------
# _batch_banner_html
# ---------------------------------------------------------------------------


def test_batch_banner_html_none() -> None:
    """Returns empty string when batch_op is None."""
    from robotsix_auto_mail.server.views.board import _batch_banner_html

    assert _batch_banner_html(None) == ""


def test_batch_banner_html_known_verb_with_progress() -> None:
    """Renders the verb label and done/total when both are integers."""
    from robotsix_auto_mail.server.views.board import _batch_banner_html

    result = _batch_banner_html({"op": "delete", "done": 120, "total": 518})
    assert 'class="batch-banner"' in result
    assert "Deleting mail: 120/518" in result
    assert "The board will refresh automatically." in result


def test_batch_banner_html_archive_verb() -> None:
    """Renders 'Archiving' for the archive verb."""
    from robotsix_auto_mail.server.views.board import _batch_banner_html

    result = _batch_banner_html({"op": "archive", "done": 3, "total": 10})
    assert "Archiving mail: 3/10" in result


def test_batch_banner_html_no_progress_when_done_none() -> None:
    """Omits the done/total part when *done* is None."""
    from robotsix_auto_mail.server.views.board import _batch_banner_html

    result = _batch_banner_html({"op": "delete", "done": None, "total": 10})
    assert "Deleting mail" in result
    # The progress ":" followed by digits is absent when done is None.
    assert ": " not in result


def test_batch_banner_html_no_progress_when_total_none() -> None:
    """Omits the done/total part when *total* is None."""
    from robotsix_auto_mail.server.views.board import _batch_banner_html

    result = _batch_banner_html({"op": "delete", "done": 5, "total": None})
    assert "Deleting mail" in result
    assert ": " not in result


def test_batch_banner_html_unknown_verb_fallback() -> None:
    """Falls back to 'Processing' when the op verb is not in BATCH_OP_VERB_LABELS."""
    from robotsix_auto_mail.server.views.board import _batch_banner_html

    result = _batch_banner_html({"op": "nuke", "done": 1, "total": 2})
    assert "Processing mail: 1/2" in result


def test_batch_banner_html_bare_running_sentinel() -> None:
    """Handles the bare running sentinel shape: op=None, done=None, total=None."""
    from robotsix_auto_mail.server.views.board import _batch_banner_html

    result = _batch_banner_html({"op": None, "done": None, "total": None})
    assert 'class="batch-banner"' in result
    assert "Processing mail" in result
    assert ": " not in result
    assert "The board will refresh automatically." in result


# ---------------------------------------------------------------------------
# _gather_account_board_data  (real temp SQLite DB)
# ---------------------------------------------------------------------------


def _make_gather_db(db_path: str) -> None:
    """Populate *db_path* with records used by _gather_account_board_data tests."""
    _populate_db(
        db_path,
        [
            {
                "message_id": "mid-inbox",
                "sender": "a@x.com",
                "subject": "Inbox mail",
                "date": "2025-01-01T00:00:00",
                "body_plain": "Body A",
                "status": "to_read",
            },
            {
                "message_id": "mid-delete",
                "sender": "b@x.com",
                "subject": "Delete mail",
                "date": "2025-01-02T00:00:00",
                "body_plain": "Body B",
                "status": "to_read",
            },
            {
                "message_id": "mid-archive-1",
                "sender": "c@x.com",
                "subject": "Archive 1",
                "date": "2025-01-03T00:00:00",
                "body_plain": "Body C",
                "status": "to_read",
            },
            {
                "message_id": "mid-archive-2",
                "sender": "d@x.com",
                "subject": "Archive 2",
                "date": "2025-01-04T00:00:00",
                "body_plain": "Body D",
                "status": "to_read",
            },
            {
                "message_id": "mid-unknown-action",
                "sender": "e@x.com",
                "subject": "Unknown",
                "date": "2025-01-05T00:00:00",
                "body_plain": "Body E",
                "status": "to_read",
            },
            {
                "message_id": "mid-notes",
                "sender": "f@x.com",
                "subject": "Notes",
                "date": "2025-01-06T00:00:00",
                "body_plain": "Body F",
                "status": "to_read",
            },
        ],
    )
    # Add a notes value via raw SQL (the _populate_db helper doesn't cover it).
    conn = init_db(db_path)
    try:
        conn.execute(
            "UPDATE mail_records SET notes = ? WHERE message_id = ?",
            ("some-note", "mid-notes"),
        )
        conn.commit()
    finally:
        conn.close()


def test_gather_batch_op_idle(single_db: str) -> None:
    """batch_op is None when the watermark is unset."""
    from robotsix_auto_mail.server.views.board import _gather_account_board_data

    _make_gather_db(single_db)
    result = _gather_account_board_data(single_db)
    assert result["batch_op"] is None


def test_gather_batch_op_idle_explicit(single_db: str) -> None:
    """batch_op is None when the watermark is 'idle'."""
    from robotsix_auto_mail.server.views.board import _gather_account_board_data

    _make_gather_db(single_db)
    conn = init_db(single_db)
    try:
        set_watermark(conn, "batch_op:state", "idle")
    finally:
        conn.close()

    result = _gather_account_board_data(single_db)
    assert result["batch_op"] is None


def test_gather_batch_op_json_payload(single_db: str) -> None:
    """batch_op parses a valid JSON progress payload."""
    from robotsix_auto_mail.server.views.board import _gather_account_board_data

    _make_gather_db(single_db)
    conn = init_db(single_db)
    try:
        set_watermark(
            conn,
            "batch_op:state",
            json.dumps({"op": "delete", "done": 3, "total": 10}),
        )
    finally:
        conn.close()

    result = _gather_account_board_data(single_db)
    assert result["batch_op"] == {"op": "delete", "done": 3, "total": 10}


def test_gather_batch_op_running_sentinel(single_db: str) -> None:
    """batch_op is the bare sentinel dict when watermark is just 'running'."""
    from robotsix_auto_mail.server.views.board import _gather_account_board_data

    _make_gather_db(single_db)
    conn = init_db(single_db)
    try:
        set_watermark(conn, "batch_op:state", "running")
    finally:
        conn.close()

    result = _gather_account_board_data(single_db)
    assert result["batch_op"] == {"op": None, "done": None, "total": None}


def test_gather_column_bucketing_no_decision(single_db: str) -> None:
    """A record with no triage decision lands in INBOX."""
    from robotsix_auto_mail.server.views.board import _gather_account_board_data

    _make_gather_db(single_db)
    result = _gather_account_board_data(single_db)
    inbox_mids = {r.message_id for r in result["column_buckets"]["INBOX"]}
    assert "mid-inbox" in inbox_mids


def test_gather_column_bucketing_known_action(single_db: str) -> None:
    """A record whose decision action is a known board column lands there."""
    from robotsix_auto_mail.server.views.board import _gather_account_board_data

    _make_gather_db(single_db)
    _seed_triage_decision(single_db, "mid-delete", action="TO_DELETE")
    _seed_triage_decision(single_db, "mid-archive-1", action="TO_ARCHIVE")

    result = _gather_account_board_data(single_db)
    delete_mids = {r.message_id for r in result["column_buckets"]["TO_DELETE"]}
    archive_mids = {r.message_id for r in result["column_buckets"]["TO_ARCHIVE"]}
    assert "mid-delete" in delete_mids
    assert "mid-archive-1" in archive_mids


def test_gather_column_bucketing_unknown_action_fallback(single_db: str) -> None:
    """A record whose decision action is NOT in _BOARD_COLUMNS falls back to
    HUMAN_TRIAGE.

    The triage_decisions table has a CHECK constraint that restricts
    actions to the known set, so we monkeypatch ``list_triage_decisions``
    to return a mock decision with an unrecognised action.
    """
    from robotsix_auto_mail.server.views.board import _gather_account_board_data

    _make_gather_db(single_db)
    # Build a mock TriageDecision with an action NOT in _BOARD_COLUMNS.
    fake_decision = mock.MagicMock()
    fake_decision.message_id = "mid-unknown-action"
    fake_decision.action = "NONEXISTENT_COLUMN"

    with mock.patch(
        "robotsix_auto_mail.server.views.board.list_triage_decisions",
        return_value=[fake_decision],
    ):
        result = _gather_account_board_data(single_db)

    ht_mids = {r.message_id for r in result["column_buckets"]["HUMAN_TRIAGE"]}
    assert "mid-unknown-action" in ht_mids


def test_gather_to_archive_sort_by_destination(single_db: str) -> None:
    """TO_ARCHIVE records are stable-sorted by archive subfolder."""
    from robotsix_auto_mail.server.views.board import _gather_account_board_data

    _make_gather_db(single_db)
    _seed_triage_decision(single_db, "mid-archive-1", action="TO_ARCHIVE")
    _seed_triage_decision(single_db, "mid-archive-2", action="TO_ARCHIVE")

    # Give mid-archive-1 a subfolder override that sorts after mid-archive-2's.
    # mid-archive-2 → "a_sub" sorts before mid-archive-1 → "z_sub".
    from robotsix_auto_mail.db import init_db as _init_db
    from robotsix_auto_mail.triage import _save_archive_overrides

    conn = _init_db(single_db)
    try:
        _save_archive_overrides(
            conn, {"mid-archive-1": "z_sub", "mid-archive-2": "a_sub"}
        )
    finally:
        conn.close()

    result = _gather_account_board_data(single_db)
    to_archive = result["column_buckets"]["TO_ARCHIVE"]
    assert len(to_archive) == 2
    # mid-archive-2 (subfolder "a_sub") should come before mid-archive-1.
    assert to_archive[0].message_id == "mid-archive-2"
    assert to_archive[1].message_id == "mid-archive-1"


def test_gather_archive_subfolders_computed(single_db: str) -> None:
    """archive_subfolders map is populated for TO_ARCHIVE records."""
    from robotsix_auto_mail.server.views.board import _gather_account_board_data

    _make_gather_db(single_db)
    _seed_triage_decision(single_db, "mid-archive-1", action="TO_ARCHIVE")
    _seed_archive_override(single_db, "mid-archive-1", "taxes/2025")

    result = _gather_account_board_data(single_db)
    assert "mid-archive-1" in result["archive_subfolders"]
    # The override is normalized — should contain "taxes/2025" or "taxes-2025".
    assert result["archive_subfolders"]["mid-archive-1"] != ""


def test_gather_folder_exists(single_db: str) -> None:
    """folder_exists is True when the subfolder path exists in archive_structure."""
    from robotsix_auto_mail.server.views.board import _gather_account_board_data

    _make_gather_db(single_db)
    _seed_triage_decision(single_db, "mid-archive-1", action="TO_ARCHIVE")
    _seed_triage_decision(single_db, "mid-archive-2", action="TO_ARCHIVE")

    # Override archive-1 to a known folder, archive-2 to an unknown one.
    _seed_archive_override(single_db, "mid-archive-1", "known-folder")
    _seed_archive_override(single_db, "mid-archive-2", "unknown-folder")

    _seed_archive_structure(
        single_db,
        [DEFAULT_ARCHIVE_ROOT, f"{DEFAULT_ARCHIVE_ROOT}/known-folder"],
    )

    result = _gather_account_board_data(single_db)
    assert result["folder_exists"]["mid-archive-1"] is True
    assert result["folder_exists"]["mid-archive-2"] is False


def test_gather_unsubscribe_suggestions(single_db: str) -> None:
    """unsubscribe_suggestions is parsed from the watermark."""
    from robotsix_auto_mail.server.views.board import _gather_account_board_data

    _make_gather_db(single_db)
    suggestions = {
        "sender@example.com": {
            "method": "mailto",
            "url": "mailto:unsub@example.com",
            "description": "Reply to unsubscribe.",
        }
    }
    conn = init_db(single_db)
    try:
        set_watermark(conn, "unsubscribe_suggestions", json.dumps(suggestions))
    finally:
        conn.close()

    result = _gather_account_board_data(single_db)
    assert result["unsubscribe_suggestions"] == suggestions


def test_gather_unsubscribe_suggestions_malformed(single_db: str) -> None:
    """Malformed JSON in unsubscribe_suggestions yields an empty dict."""
    from robotsix_auto_mail.server.views.board import _gather_account_board_data

    _make_gather_db(single_db)
    conn = init_db(single_db)
    try:
        set_watermark(conn, "unsubscribe_suggestions", "not-json")
    finally:
        conn.close()

    result = _gather_account_board_data(single_db)
    assert result["unsubscribe_suggestions"] == {}


def test_gather_record_notes(single_db: str) -> None:
    """record_notes maps message_id to notes for records that have them."""
    from robotsix_auto_mail.server.views.board import _gather_account_board_data

    _make_gather_db(single_db)
    result = _gather_account_board_data(single_db)
    assert result["record_notes"] == {"mid-notes": "some-note"}
    # Records without notes are absent.
    assert "mid-inbox" not in result["record_notes"]


def test_gather_triage_running_false(single_db: str) -> None:
    """triage_running is False when watermark is unset."""
    from robotsix_auto_mail.server.views.board import _gather_account_board_data

    _make_gather_db(single_db)
    result = _gather_account_board_data(single_db)
    assert result["triage_running"] is False


def test_gather_triage_running_true(single_db: str) -> None:
    """triage_running is True when watermark is 'running'."""
    from robotsix_auto_mail.server.views.board import _gather_account_board_data

    _make_gather_db(single_db)
    conn = init_db(single_db)
    try:
        set_watermark(conn, "triage_run:state", "running")
    finally:
        conn.close()

    result = _gather_account_board_data(single_db)
    assert result["triage_running"] is True


def test_gather_returns_all_expected_keys(single_db: str) -> None:
    """The returned dict contains all expected keys."""
    from robotsix_auto_mail.server.views.board import _gather_account_board_data

    _make_gather_db(single_db)
    result = _gather_account_board_data(single_db)
    assert set(result.keys()) == {
        "triage_running",
        "batch_op",
        "triage_by_mid",
        "column_buckets",
        "archive_subfolders",
        "archive_folders",
        "folder_exists",
        "unsubscribe_suggestions",
        "record_notes",
    }


def test_gather_archive_folders_stripped(single_db: str) -> None:
    """archive_folders strips the root prefix from each folder name."""
    from robotsix_auto_mail.server.views.board import _gather_account_board_data

    _make_gather_db(single_db)
    root = DEFAULT_ARCHIVE_ROOT
    _seed_archive_structure(
        single_db,
        [root, f"{root}/sub1", f"{root}/sub1/nested", f"{root}/sub2"],
    )
    result = _gather_account_board_data(single_db)
    assert "sub1" in result["archive_folders"]
    assert "sub1/nested" in result["archive_folders"]
    assert "sub2" in result["archive_folders"]
    # The root itself is excluded.
    assert "" not in result["archive_folders"]
    assert root not in result["archive_folders"]


def test_gather_unsubscribe_empty_when_unset(single_db: str) -> None:
    """unsubscribe_suggestions is empty dict when watermark is unset."""
    from robotsix_auto_mail.server.views.board import _gather_account_board_data

    _make_gather_db(single_db)
    result = _gather_account_board_data(single_db)
    assert result["unsubscribe_suggestions"] == {}


# ---------------------------------------------------------------------------
# _render_board_page_shell
# ---------------------------------------------------------------------------


def _page_shell_sentinels(**overrides: Any) -> str:
    """Call ``_render_board_page_shell`` with sentinel defaults for each
    keyword argument, overridden by *overrides*."""
    from robotsix_auto_mail.server.views.board import _render_board_page_shell

    defaults: dict[str, Any] = {
        "columns_html": "<div>COLUMNS</div>",
        "triage_running": False,
        "picker_html": "<select>PICKER</select>",
        "account_qs": "",
        "fetch_qs": "?a=1",
        "batch_control_html": "<div>BATCH</div>",
        "data_account_js": False,
    }
    defaults.update(overrides)
    return _render_board_page_shell(**defaults)


def test_page_shell_injects_columns_html() -> None:
    """The columns_html argument is placed inside the .board wrapper."""
    result = _page_shell_sentinels(columns_html="<div>CUSTOM_COL</div>")
    assert "<div>CUSTOM_COL</div>" in result


def test_page_shell_injects_picker_html() -> None:
    """The picker_html argument appears in the page body."""
    result = _page_shell_sentinels(picker_html="<select>MY_PICKER</select>")
    assert "<select>MY_PICKER</select>" in result


def test_page_shell_injects_batch_control_html() -> None:
    """The batch_control_html argument appears inside #batch-control."""
    result = _page_shell_sentinels(batch_control_html="<div>BATCH_PROGRESS</div>")
    assert '<span id="batch-control"><div>BATCH_PROGRESS</div></span>' in result


def test_page_shell_triage_running_true() -> None:
    """When triage_running is True a triage banner is rendered."""
    result = _page_shell_sentinels(triage_running=True)
    assert 'class="triage-banner"' in result
    assert "Triage is currently running" in result


def test_page_shell_triage_running_false() -> None:
    """When triage_running is False no triage banner is rendered."""
    result = _page_shell_sentinels(triage_running=False)
    assert 'class="triage-banner"' not in result
    assert "Triage is currently running" not in result


def test_page_shell_data_account_js_true() -> None:
    """data_account_js is True in the JS config when the arg is True."""
    result = _page_shell_sentinels(data_account_js=True)
    # The board-config JSON payload.
    assert '"data_account_js": true' in result


def test_page_shell_data_account_js_false() -> None:
    """data_account_js is False in the JS config when the arg is False."""
    result = _page_shell_sentinels(data_account_js=False)
    assert '"data_account_js": false' in result


def test_page_shell_account_qs_wired() -> None:
    """The account_qs is reflected in the JS config."""
    result = _page_shell_sentinels(account_qs="&account=xyz")
    assert '"account_qs": "&account=xyz"' in result


def test_page_shell_fetch_qs_wired() -> None:
    """The fetch_qs is reflected in the JS config."""
    result = _page_shell_sentinels(fetch_qs="?account=__all__")
    assert '"fetch_qs": "?account=__all__"' in result


def test_page_shell_html5_doctype() -> None:
    """The page shell starts with the HTML5 doctype."""
    result = _page_shell_sentinels()
    assert result.startswith("<!DOCTYPE html>")


def test_page_shell_contains_title() -> None:
    """The page shell has a <title>Mail Board</title>."""
    result = _page_shell_sentinels()
    assert "<title>Mail Board</title>" in result


def test_page_shell_contains_board_js() -> None:
    """The page shell loads board.js and board-auto-mail.js."""
    result = _page_shell_sentinels()
    assert 'src="/static/board.js"' in result
    assert 'src="/static/board-auto-mail.js"' in result


def test_page_shell_contains_side_panel() -> None:
    """The page shell includes auto-mail's side-panel skeleton."""
    result = _page_shell_sentinels()
    assert 'class="side-panel"' in result
    assert 'id="side-panel"' in result
    assert 'class="close-btn"' in result


# ---------------------------------------------------------------------------
# _build_board_html  (via monkeypatching helpers)
# ---------------------------------------------------------------------------


def test_build_board_html_patches_through() -> None:
    """_build_board_html assembles the shell from its content/banner/shell
    helpers (verified by patching each to a unique sentinel)."""
    from robotsix_auto_mail.server.views import _build_board_html

    sentinel_content = {
        "columns_html": "<div>FAKE_COLS</div>",
        "triage_running": True,
        "batch_op": {"op": "delete", "done": 1, "total": 2},
    }
    sentinel_shell = "<html>SHELL_SENTINEL</html>"

    with (
        mock.patch(
            "robotsix_auto_mail.server.views.board._build_board_content",
            return_value=sentinel_content,
        ),
        mock.patch(
            "robotsix_auto_mail.server.views.board._render_board_page_shell",
            return_value=sentinel_shell,
        ),
    ):
        result = _build_board_html("/fake/path.db")

    assert result == sentinel_shell


def test_build_board_html_single_account_no_picker() -> None:
    """_build_board_html with no accounts yields an empty picker_html and
    data_account_js=False."""
    from robotsix_auto_mail.server.views import _build_board_html

    with mock.patch(
        "robotsix_auto_mail.server.views.board._build_board_content",
        return_value={
            "columns_html": "",
            "triage_running": False,
            "batch_op": None,
        },
    ):
        with mock.patch(
            "robotsix_auto_mail.server.views.board._render_board_page_shell",
        ) as mock_shell:
            _build_board_html("/fake/path.db")

    kwargs = mock_shell.call_args.kwargs
    assert kwargs["picker_html"] == ""
    assert kwargs["data_account_js"] is False
    assert kwargs["account_qs"] == ""


def test_build_board_html_multi_account_has_picker_and_qs() -> None:
    """_build_board_html with ≥2 accounts renders a picker and wires
    account_qs/fetch_qs."""
    from robotsix_auto_mail.server.views import _build_board_html

    accounts = MailAccountsConfig(
        accounts=(
            MailAccount(
                account_id="A",
                config=_dummy_mail_config("/fake/a.db"),
                label="Acc A",
            ),
            MailAccount(
                account_id="B",
                config=_dummy_mail_config("/fake/b.db"),
                label="Acc B",
            ),
        ),
        default_account_id="A",
    )

    with mock.patch(
        "robotsix_auto_mail.server.views.board._build_board_content",
        return_value={
            "columns_html": "",
            "triage_running": False,
            "batch_op": None,
        },
    ):
        with mock.patch(
            "robotsix_auto_mail.server.views.board._render_board_page_shell",
        ) as mock_shell:
            _build_board_html("/fake/a.db", accounts=accounts, current_account_id="A")

    kwargs = mock_shell.call_args.kwargs
    assert kwargs["picker_html"] != ""
    assert "All mailboxes" in kwargs["picker_html"]
    assert "Acc A" in kwargs["picker_html"]
    assert "Acc B" in kwargs["picker_html"]
    # With a current_account_id set, account_qs and fetch_qs are wired.
    assert "account=" in kwargs["account_qs"]
    assert "?account=" in kwargs["fetch_qs"]


# ---------------------------------------------------------------------------
# _build_global_board_html  (via monkeypatching helpers)
# ---------------------------------------------------------------------------


def test_build_global_board_html_patches_through() -> None:
    """_build_global_board_html assembles the shell via its helpers."""
    from robotsix_auto_mail.server.views import _build_global_board_html

    sentinel_content = {
        "columns_html": "<div>GLOBAL_COLS</div>",
        "triage_running": False,
        "batch_op": None,
    }
    sentinel_shell = "<html>GLOBAL_SHELL</html>"

    accounts = MailAccountsConfig(
        accounts=(
            MailAccount(
                account_id="A",
                config=_dummy_mail_config("/fake/a.db"),
                label="A One",
            ),
        ),
        default_account_id="A",
    )

    with (
        mock.patch(
            "robotsix_auto_mail.server.views.board._build_global_board_content",
            return_value=sentinel_content,
        ),
        mock.patch(
            "robotsix_auto_mail.server.views.board._render_board_page_shell",
            return_value=sentinel_shell,
        ),
    ):
        result = _build_global_board_html(accounts)

    assert result == sentinel_shell


def test_build_global_board_html_data_account_and_picker() -> None:
    """_build_global_board_html passes data_account_js=True and a picker
    with 'All mailboxes' selected."""
    from robotsix_auto_mail.server.views import _build_global_board_html

    accounts = MailAccountsConfig(
        accounts=(
            MailAccount(
                account_id="X",
                config=_dummy_mail_config("/fake/x.db"),
                label="X Label",
            ),
            MailAccount(
                account_id="Y",
                config=_dummy_mail_config("/fake/y.db"),
                label=None,
            ),
        ),
        default_account_id="X",
    )

    with mock.patch(
        "robotsix_auto_mail.server.views.board._build_global_board_content",
        return_value={
            "columns_html": "",
            "triage_running": False,
            "batch_op": None,
        },
    ):
        with mock.patch(
            "robotsix_auto_mail.server.views.board._render_board_page_shell",
        ) as mock_shell:
            _build_global_board_html(accounts)

    kwargs = mock_shell.call_args.kwargs
    assert kwargs["data_account_js"] is True
    assert kwargs["account_qs"] == ""  # no page-level account_qs
    assert kwargs["fetch_qs"] == "?account=__all__"
    picker = kwargs["picker_html"]
    assert "All mailboxes" in picker
    assert '__all__" selected' in picker
    assert "X Label" in picker
    assert "Y" in picker  # falls back to account_id when label is None


def test_build_global_board_html_batch_banner_wired() -> None:
    """The batch banner is wired through when batch_op is present."""
    from robotsix_auto_mail.server.views import _build_global_board_html

    accounts = MailAccountsConfig(
        accounts=(
            MailAccount(
                account_id="Z",
                config=_dummy_mail_config("/fake/z.db"),
                label="Z",
            ),
        ),
        default_account_id="Z",
    )

    with mock.patch(
        "robotsix_auto_mail.server.views.board._build_global_board_content",
        return_value={
            "columns_html": "",
            "triage_running": False,
            "batch_op": {"op": "archive", "done": 5, "total": 20},
        },
    ):
        with mock.patch(
            "robotsix_auto_mail.server.views.board._render_board_page_shell",
        ) as mock_shell:
            _build_global_board_html(accounts)

    kwargs = mock_shell.call_args.kwargs
    assert "Archiving mail: 5/20" in kwargs["batch_control_html"]


# ---------------------------------------------------------------------------
# Helpers
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


def _dummy_mail_config(db_path: str) -> Any:
    """A minimal MailConfig bound to *db_path*."""
    from robotsix_auto_mail.config import MailConfig

    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="u@example.com",
        password="p",
        db_path=db_path,
        archive_enabled=False,
        triage_on_ingest=False,
    )
