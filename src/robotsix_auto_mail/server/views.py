"""HTML/view renderers for the board server."""

from __future__ import annotations

import html
import json
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import quote

from robotsix_board import render_board

from robotsix_auto_mail.config import DEFAULT_ARCHIVE_ROOT, MailAccountsConfig
from robotsix_auto_mail.db import MailRecord, list_records
from robotsix_auto_mail.format import _effective_body_plain, _format_date
from robotsix_auto_mail.server._constants import _BOARD_COLUMNS
from robotsix_auto_mail.server.adapters import _NonEmptyColumnsAdapter
from robotsix_auto_mail.server.board_adapter import MailBoardAdapter
from robotsix_auto_mail.triage import (
    TRIAGE_ACTION_LABELS,
    TRIAGE_ACTION_ORDER,
    TriageDecision,
    get_archive_subfolder,
    get_triage_decision,
    list_triage_decisions,
)


def _render_board_columns(
    adapter: MailBoardAdapter, cards: Mapping[str, list[MailRecord]]
) -> str:
    """Return the inner ``.board-column`` markup produced by ``render_board``.

    ``render_board`` wraps the columns in a ``<div id="board" class="board">``
    element and appends a ``#drawer`` shell that auto-mail does not use (it
    has its own ``.side-panel``).  Both the ``/board`` page shell and the
    ``/board-content`` refresh endpoint expect just the inner column markup
    — to be injected into auto-mail's own ``.board`` wrapper — so this
    helper strips the library's outer wrapper and drawer.  Returns the
    empty-board placeholder when no column is populated.
    """
    ordered = [key for key, _label in adapter.columns() if key in cards]
    if not ordered:
        return '<div class="empty-board">No mail yet.</div>'
    full = render_board(cast("Any", _NonEmptyColumnsAdapter(adapter, ordered)), cards)
    open_tag = '<div id="board" class="board">'
    drawer = (
        '<div id="drawer" class="drawer hidden">'
        '<div class="drawer-content"></div>'
        "</div>"
    )
    inner = full
    if inner.startswith(open_tag):
        inner = inner[len(open_tag) :]
    drawer_idx = inner.rfind(drawer)
    if drawer_idx != -1:
        inner = inner[:drawer_idx]
    inner = inner.rstrip()
    if inner.endswith("</div>"):
        inner = inner[: -len("</div>")]
    return inner.strip("\n")


def _gather_account_board_data(
    db_path: str,
    archive_root: str = DEFAULT_ARCHIVE_ROOT,
) -> dict[str, Any]:
    """Read one account's DB and return the raw structures for board building.

    Returns a dict with keys: ``triage_running``, ``batch_op``,
    ``triage_by_mid``, ``column_buckets``, ``proposals``,
    ``archive_subfolders``, ``folder_exists``, ``unsubscribe_suggestions``,
    ``record_notes``.

    This is the DB-reading half of :func:`_build_board_content`, extracted
    so the global board can call it per-account.
    """
    from robotsix_auto_mail.db import get_watermark, init_db

    conn = init_db(db_path, skip_migrations=True)
    try:
        # Check whether the triage agent is currently running so the
        # board can show a visual indicator and disable the button.
        triage_running = get_watermark(conn, "triage_run:state") == "running"

        # Parse the batch-op watermark (delete/archive progress).  The
        # value is ``"idle"``/``None`` when no batch op is running, else a
        # JSON ``{"op", "done", "total"}`` progress payload.
        batch_raw = get_watermark(conn, "batch_op:state")
        batch_op: dict[str, Any] | None = None
        if batch_raw is not None and batch_raw != "idle":
            try:
                parsed = json.loads(batch_raw)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                batch_op = {
                    "op": parsed.get("op"),
                    "done": parsed.get("done"),
                    "total": parsed.get("total"),
                }
            else:
                # A bare "running" sentinel (set by the handler before the
                # worker writes its first JSON payload) still counts as a
                # running batch op with as-yet-unknown counts.
                batch_op = {"op": None, "done": None, "total": None}

        # Gather every record and every triage decision once.
        all_records = list_records(conn)
        triage_by_mid: dict[str, TriageDecision] = {
            decision.message_id: decision for decision in list_triage_decisions(conn)
        }
        # Bucket records into columns by their triage-decision action.
        # Untriaged records land in the ``"INBOX"`` column.
        column_buckets: dict[str, list[MailRecord]] = {
            action: [] for action in _BOARD_COLUMNS
        }
        for record in all_records:
            decision = triage_by_mid.get(record.message_id)
            if decision is not None:
                column = decision.action
                # Guard: an unrecognised action lands in HUMAN_TRIAGE.
                if column not in column_buckets:
                    column = "HUMAN_TRIAGE"
            else:
                column = "INBOX"
            column_buckets[column].append(record)

        # -- archive proposal context -------------------------------------
        # Read the archive_structure watermark to know which folders exist.
        archive_raw = get_watermark(conn, "archive_structure")
        existing_folders: set[str] = set()
        delimiter: str = "/"
        effective_root: str = archive_root
        if archive_raw is not None:
            try:
                data = json.loads(archive_raw)
                if isinstance(data, list):
                    # Old format: bare list of folder names.
                    existing_folders = set(data)
                    delimiter = "/"
                    effective_root = data[0] if data else archive_root
                else:
                    # New format: {"delimiter": ..., "folders": [...]}.
                    existing_folders = set(data["folders"])
                    delimiter = data.get("delimiter", "/")
                    effective_root = (
                        data["folders"][0] if data["folders"] else archive_root
                    )
            except (json.JSONDecodeError, TypeError, KeyError):
                # Malformed archive_structure watermark — keep the empty
                # folder set / "/" delimiter / archive_root defaults set above.
                pass

        # Compute effective subfolder for each TO_ARCHIVE record.
        archive_subfolders: dict[str, str] = {}
        folder_exists: dict[str, bool] = {}
        for record in column_buckets.get("TO_ARCHIVE", []):
            subfolder = get_archive_subfolder(conn, record.message_id, record)
            archive_subfolders[record.message_id] = subfolder
            if subfolder:
                translated = subfolder.replace("/", delimiter)
                full_path = f"{effective_root}{delimiter}{translated}"
            else:
                full_path = effective_root
            folder_exists[record.message_id] = full_path in existing_folders

        # Order TO_ARCHIVE cards by destination so the board JS renders
        # contiguous per-folder groups (each with an "Archive these" button).
        # ``list.sort`` is stable, so the prior within-folder order is kept.
        to_archive_bucket = column_buckets.get("TO_ARCHIVE")
        if to_archive_bucket:
            to_archive_bucket.sort(
                key=lambda r: archive_subfolders.get(r.message_id, "")
            )

        # Order TO_ARCHIVE cards by destination so the board JS can render
        # contiguous per-folder groups (stable sort preserves the existing
        # date order within each destination).
        if column_buckets.get("TO_ARCHIVE"):
            column_buckets["TO_ARCHIVE"].sort(
                key=lambda r: archive_subfolders.get(r.message_id, "")
            )

        # -- unsubscribe suggestions for TO_DELETE column -----------------
        suggestions_raw = get_watermark(conn, "unsubscribe_suggestions")
        unsubscribe_suggestions: dict[str, dict[str, Any]] = {}
        if suggestions_raw is not None:
            try:
                unsubscribe_suggestions = json.loads(suggestions_raw)
            except (json.JSONDecodeError, TypeError):
                # Malformed unsubscribe_suggestions watermark — leave the
                # empty suggestions dict initialised above.
                pass

        # Build record_notes map for notes indicators.
        record_notes: dict[str, str] = {
            r.message_id: r.notes for r in all_records if r.notes
        }
    finally:
        conn.close()

    return {
        "triage_running": triage_running,
        "batch_op": batch_op,
        "triage_by_mid": triage_by_mid,
        "column_buckets": column_buckets,
        "archive_subfolders": archive_subfolders,
        "folder_exists": folder_exists,
        "unsubscribe_suggestions": unsubscribe_suggestions,
        "record_notes": record_notes,
    }


def _build_board_content(
    db_path: str,
    archive_root: str = DEFAULT_ARCHIVE_ROOT,
) -> dict[str, Any]:
    """Return ``{"columns_html": …, "triage_running": …}``.

    Opens a fresh database connection, gathers every mail record and
    buckets them into kanban columns based on each record's triage
    decision.  Cards with no triage decision land in the ``"INBOX"``
    column.  Renders column and rule-proposal HTML fragments and
    returns them as a plain dict.

    Raises ``Exception`` when the database cannot be opened (the
    caller should catch it and return a 503).
    """
    gathered = _gather_account_board_data(db_path, archive_root=archive_root)

    triage_running = gathered["triage_running"]
    batch_op = gathered["batch_op"]
    triage_by_mid: dict[str, TriageDecision] = gathered["triage_by_mid"]
    column_buckets: dict[str, list[MailRecord]] = gathered["column_buckets"]
    archive_subfolders: dict[str, str] = gathered["archive_subfolders"]
    folder_exists: dict[str, bool] = gathered["folder_exists"]
    unsubscribe_suggestions: dict[str, dict[str, Any]] = gathered[
        "unsubscribe_suggestions"
    ]
    record_notes: dict[str, str] = gathered["record_notes"]

    # Build the board adapter — the single source of truth for both the
    # base column/card scaffold (the protocol methods) and auto-mail's
    # custom per-card / per-column widgets, which the library appends via
    # the duck-typed ``card_extra_html`` / ``column_extra_html`` hooks.
    adapter = MailBoardAdapter(
        triage_by_mid={mid: d.action for mid, d in triage_by_mid.items()},
        archive_subfolders=archive_subfolders,
        folder_exists=folder_exists,
        archive_root=archive_root,
        unsubscribe_suggestions=unsubscribe_suggestions,
        record_notes=record_notes,
        column_records=column_buckets,
        batch_running=batch_op is not None,
    )
    # auto-mail hides empty columns, so only the populated buckets are
    # handed to ``render_board`` (which renders one column per adapter
    # column).
    cards: dict[str, list[MailRecord]] = {
        action: column_buckets[action]
        for action in _BOARD_COLUMNS
        if column_buckets[action]
    }
    columns_html = _render_board_columns(adapter, cards)

    return {
        "columns_html": columns_html,
        "triage_running": triage_running,
        "batch_op": batch_op,
        "unsubscribe_suggestions": unsubscribe_suggestions,
    }


def _build_global_board_content(
    accounts: MailAccountsConfig,
) -> dict[str, Any]:
    """Aggregate board content across all configured accounts.

    Calls :func:`_gather_account_board_data` for each account, merges the
    per-account column buckets, and builds a single :class:`MailBoardAdapter`
    with the merged context plus ``record_accounts`` / ``account_labels``
    maps so per-card widgets route to the owning account's DB.

    Returns the same JSON shape as :func:`_build_board_content`
    (``columns_html``, ``proposals_html``, ``triage_running``,
    ``unsubscribe_suggestions``).

    Assumption: ``message_id`` is treated as globally unique (RFC 5322).
    Cross-account duplicate message_ids are a known limitation and out of
    scope.
    """
    # Per-account merge accumulators.
    merged_buckets: dict[str, list[MailRecord]] = {
        action: [] for action in _BOARD_COLUMNS
    }
    merged_triage_by_mid: dict[str, str] = {}
    merged_archive_subfolders: dict[str, str] = {}
    merged_folder_exists: dict[str, bool] = {}
    merged_unsubscribe: dict[str, dict[str, Any]] = {}
    merged_record_notes: dict[str, str] = {}
    record_accounts: dict[str, str] = {}
    account_labels: dict[str, str] = {}
    triage_running: bool = False

    for account in accounts.accounts:
        aid = account.account_id
        label = account.label if account.label else aid
        account_labels[aid] = label

        gathered = _gather_account_board_data(
            account.config.db_path,
            archive_root=account.config.archive_root,
        )

        triage_running = triage_running or gathered["triage_running"]

        # Merge column buckets.
        for action in _BOARD_COLUMNS:
            merged_buckets[action].extend(gathered["column_buckets"][action])

        # Track per-message owning account.
        for recs in gathered["column_buckets"].values():
            for rec in recs:
                record_accounts[rec.message_id] = aid

        # Merge context maps.
        merged_triage_by_mid.update(
            {mid: d.action for mid, d in gathered["triage_by_mid"].items()}
        )
        merged_archive_subfolders.update(gathered["archive_subfolders"])
        merged_folder_exists.update(gathered["folder_exists"])
        merged_unsubscribe.update(gathered["unsubscribe_suggestions"])
        merged_record_notes.update(gathered["record_notes"])

    adapter = MailBoardAdapter(
        triage_by_mid=merged_triage_by_mid,
        archive_subfolders=merged_archive_subfolders,
        folder_exists=merged_folder_exists,
        archive_root=DEFAULT_ARCHIVE_ROOT,  # not used per-card in aggregate mode
        unsubscribe_suggestions=merged_unsubscribe,
        record_notes=merged_record_notes,
        column_records=merged_buckets,
        batch_running=False,  # batch ops are per-account, suppressed in aggregate
        record_accounts=record_accounts,
        account_labels=account_labels,
    )

    cards: dict[str, list[MailRecord]] = {
        action: merged_buckets[action]
        for action in _BOARD_COLUMNS
        if merged_buckets[action]
    }
    columns_html = _render_board_columns(adapter, cards)

    return {
        "columns_html": columns_html,
        "triage_running": triage_running,
        "unsubscribe_suggestions": merged_unsubscribe,
    }


def _batch_banner_html(batch_op: dict[str, Any] | None) -> str:
    """Return the ``.batch-banner`` markup for a running batch op, or ``""``.

    Renders the operation verb plus ``done/total`` progress (e.g.
    ``"Deleting mail: 120/518. The board will refresh automatically."``).
    Returns the empty string when *batch_op* is ``None`` (no op running).
    """
    if batch_op is None:
        return ""
    verb = "Archiving" if batch_op.get("op") == "archive" else "Deleting"
    done = batch_op.get("done")
    total = batch_op.get("total")
    if isinstance(done, int) and isinstance(total, int):
        progress = f": {done}/{total}"
    else:
        progress = ""
    return (
        '<div class="batch-banner">'
        f"{verb} mail{progress}. The board will refresh automatically."
        "</div>"
    )


def _render_board_page_shell(
    *,
    columns_html: str,
    triage_running: bool,
    picker_html: str,
    account_qs: str,
    fetch_qs: str,
    batch_control_html: str,
    data_account_js: bool,
) -> str:
    """Shared HTML page shell + JS for both single-account and global boards.

    All varying parts are passed as keyword arguments so the two callers
    (:func:`_build_board_html` and :func:`_build_global_board_html`) share
    the ~150-line shell without duplication.
    """
    triage_control_html: str
    if triage_running:
        triage_control_html = (
            '<div class="triage-banner">'
            "Triage is currently running. The board will refresh automatically."
            "</div>"
        )
    else:
        triage_control_html = ""

    # Build the #board-config JSON payload so robotsix-board's board.js
    # can activate and expose its public API (robotsixBoardRefresh, etc.).
    # Auto-mail's own board-auto-mail.js overlay composes on top via
    # capture-phase interceptor and reads its app-specific config from
    # the same element.
    board_config = {
        "render_mode": "json_hydration",
        "columns": [
            [action, TRIAGE_ACTION_LABELS[action]] for action in TRIAGE_ACTION_ORDER
        ],
        "account_qs": account_qs,
        "fetch_qs": fetch_qs,
        "data_account_js": data_account_js,
    }

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<title>Mail Board</title>\n"
        '<link rel="stylesheet" href="/static/board.css">\n'
        '<link rel="stylesheet" href="/static/automail/board.css">\n'
        "</head>\n"
        "<body>\n"
        "<h1>Mail Board</h1>\n"
        f'<span id="triage-control">{triage_control_html}</span>\n'
        f'<span id="batch-control">{batch_control_html}</span>\n'
        f"{picker_html}\n"
        '<div class="board-wrapper">\n'
        '<div class="board">\n'
        f"{columns_html}"
        "\n</div>\n"
        "</div>\n"
        # Side-panel skeleton (auto-mail's iframe-based pattern, not
        # the library's #drawer).
        '<div class="side-panel" id="side-panel">\n'
        '<div class="panel-header">\n'
        '<span class="panel-title"></span>\n'
        '<button class="close-btn" onclick="closeDetail()">&times;</button>\n'
        "</div>\n"
        '<iframe src="" title="Mail detail"></iframe>\n'
        "</div>\n"
        # board.js configuration — enables the library's public API and
        # harmless internal handlers (no #board element → all no-ops).
        '<script id="board-config" type="application/json">'
        f"{json.dumps(board_config)}"
        "</script>\n"
        '<script src="/static/board.js"></script>\n'
        # App-specific overlay — must load after board.js so the
        # capture-phase interceptor can override board.js's bubble
        # handler for card clicks.
        '<script src="/static/board-auto-mail.js"></script>\n'
        "</body>\n"
        "</html>"
    )


def _build_board_html(
    db_path: str,
    archive_root: str = DEFAULT_ARCHIVE_ROOT,
    *,
    accounts: MailAccountsConfig | None = None,
    current_account_id: str | None = None,
) -> str:
    """Build the full ``/board`` HTML document.

    Calls :func:`_build_board_content` and wraps the result in a minimal
    HTML5 page shell (Python f-strings, no Jinja2).  Raises ``Exception``
    when the database cannot be opened (the caller should catch it and
    return a 503).

    When *accounts* describes two or more accounts an account picker is
    rendered in the header and the resolved *current_account_id* is
    threaded into the JS-built GET URLs (detail iframe, content refresh)
    so deep-links and cookie-less clients route correctly.  In the
    single-account / legacy path (*accounts* ``None`` or fewer than two
    accounts) the served HTML is byte-for-byte unchanged apart from an
    empty picker slot.
    """
    content = _build_board_content(db_path, archive_root=archive_root)

    # -- account picker + URL threading -----------------------------------
    # The picker only appears when more than one account is configured.
    # Switching accounts navigates to ``/board?account=<id>`` which the
    # already-landed ``_select_account`` resolves and persists via the
    # ``account`` cookie — every subsequent same-origin request (POST
    # forms, iframe, fetch) then routes to the chosen account.  POST form
    # bodies deliberately carry no account field: ``_select_account``
    # reads only the URL query or the cookie, so a hidden field would be
    # ignored; the cookie is the persistence mechanism.
    picker_html = ""
    account_qs = ""
    fetch_qs = ""
    multi_account = accounts is not None and len(accounts.ids()) >= 2
    if multi_account and accounts is not None:
        options_parts: list[str] = ['<option value="__all__">All mailboxes</option>']
        for account_id in accounts.ids():
            account = accounts.get(account_id)
            display = account.label if account.label else account.account_id
            sel = " selected" if account_id == current_account_id else ""
            options_parts.append(
                f'<option value="{html.escape(account_id)}"{sel}>'
                f"{html.escape(display)}</option>"
            )
        picker_html = (
            '<select id="account-picker"'
            " onchange=\"window.location.href='/board?account='"
            '+encodeURIComponent(this.value)">'
            f"{''.join(options_parts)}"
            "</select>"
        )
    if multi_account and current_account_id:
        account_qs = "&account=" + quote(current_account_id, safe="")
        fetch_qs = "?account=" + quote(current_account_id, safe="")

    batch_control_html = _batch_banner_html(content["batch_op"])

    return _render_board_page_shell(
        columns_html=content["columns_html"],
        triage_running=content["triage_running"],
        picker_html=picker_html,
        account_qs=account_qs,
        fetch_qs=fetch_qs,
        batch_control_html=batch_control_html,
        data_account_js=False,
    )


def _build_global_board_html(
    accounts: MailAccountsConfig,
    *,
    current_account_id: str = "__all__",
) -> str:
    """Build the full ``/board`` HTML document for the aggregate view.

    Renders every configured account's records in a single unified board,
    with a picker whose first option is ``All mailboxes`` (selected by
    default).  Card click handlers derive the owning account from
    ``data-account`` rather than a page-level ``account_qs`` so each
    per-card detail link and form routes to the correct single-account DB.

    Folder-triage is hidden in the aggregate view (it requires a single
    account's IMAP connection).
    """
    content = _build_global_board_content(accounts)

    # Account picker — first option is the global view.
    options_parts: list[str] = [
        '<option value="__all__" selected>All mailboxes</option>'
    ]
    for account_id in accounts.ids():
        account = accounts.get(account_id)
        display = account.label if account.label else account.account_id
        options_parts.append(
            f'<option value="{html.escape(account_id)}">{html.escape(display)}</option>'
        )
    picker_html = (
        '<select id="account-picker"'
        " onchange=\"window.location.href='/board?account='"
        '+encodeURIComponent(this.value)">'
        f"{''.join(options_parts)}"
        "</select>"
    )

    # Aggregate view always polls /board-content?account=__all__ and
    # has no page-level account_qs for the detail iframe (each card
    # carries its own data-account).
    fetch_qs = "?account=__all__"

    return _render_board_page_shell(
        columns_html=content["columns_html"],
        triage_running=content["triage_running"],
        picker_html=picker_html,
        account_qs="",  # page-level; cards carry their own
        fetch_qs=fetch_qs,
        batch_control_html="",  # batch ops are per-account, suppressed
        data_account_js=True,
    )


def _build_detail_html(
    db_path: str,
    message_id: str,
    *,
    embed: bool = False,
    focus_draft: bool = False,
) -> str | None:
    """Build a full HTML detail page for a single ``MailRecord``.

    Returns the HTML string, or ``None`` when *message_id* is not found.
    Raises an exception on database errors (caller catches for 503).
    """
    from robotsix_auto_mail.db import get_record_by_message_id, init_db

    conn = init_db(db_path, skip_migrations=True)
    try:
        record = get_record_by_message_id(conn, message_id)
        triage_decision = get_triage_decision(conn, message_id)
    finally:
        conn.close()

    if record is None:
        return None

    # Parse JSON fields
    try:
        recipients = json.loads(record.recipients_json)
    except (json.JSONDecodeError, TypeError):
        recipients = {"to": [], "cc": []}
    to_list: list[str] = (
        recipients.get("to", []) if isinstance(recipients, dict) else []
    )
    cc_list: list[str] = (
        recipients.get("cc", []) if isinstance(recipients, dict) else []
    )

    try:
        attachments = json.loads(record.attachments_json)
    except (json.JSONDecodeError, TypeError):
        attachments = []
    if not isinstance(attachments, list):
        attachments = []

    # Status options — drive from triage decision, not mail_records.status.
    if triage_decision is not None:
        current_action = triage_decision.action
    else:
        current_action = "INBOX"

    quoted_mid = quote(record.message_id, safe="")
    redirect_input = ""
    if embed:
        redirect_input = (
            '<input type="hidden" name="redirect_to"'
            f' value="/email/{html.escape(quoted_mid)}?embed=1">'
        )
    move_form = _render_move_form(record, current_action, redirect_input)

    # Subject for title (truncated to ~60 chars)
    raw_subject = record.subject.strip() or "(no subject)"
    title_subject = raw_subject[:60] + ("…" if len(raw_subject) > 60 else "")

    # Date
    date_str = html.escape(_format_date(record.date))

    body_html_render, body_html_note = _render_body(record)
    notes_section = _render_notes_section(record, redirect_input)
    draft_section = _render_draft_section(
        record, current_action, focus_draft, redirect_input
    )
    to_html, cc_section = _render_recipients(to_list, cc_list)
    attach_html = _render_attachments(attachments)
    imap_uid_section = _render_imap_uid_section(record)
    triage_section = _render_triage_section(triage_decision)

    # The inner detail fields (Sender through IMAP UID) are identical for
    # the embedded fragment and the full standalone page.
    fields_html = (
        '<div class="detail-field">'
        '<div class="detail-label">Sender</div>'
        f'<div class="detail-value"><strong>{html.escape(record.sender)}'
        "</strong></div>"
        "</div>\n"
        '<div class="detail-field">'
        '<div class="detail-label">Date</div>'
        f'<div class="detail-value">{date_str}</div>'
        "</div>\n"
        '<div class="detail-field">'
        '<div class="detail-label">Status</div>'
        f'<div class="detail-value">{html.escape(TRIAGE_ACTION_LABELS[current_action])}'
        f"{move_form}</div>"
        "</div>\n"
        f"{triage_section}"
        '<div class="detail-field">'
        '<div class="detail-label">To</div>'
        f'<div class="detail-value">{to_html}</div>'
        "</div>\n"
        f"{cc_section}"
        '<div class="detail-field">'
        '<div class="detail-label">Body</div>'
        f'<div class="detail-value">{body_html_render}</div>'
        "</div>\n"
        f"{body_html_note}"
        f"{notes_section}"
        f"{draft_section}"
        '<div class="detail-field">'
        '<div class="detail-label">Attachments</div>'
        f'<div class="detail-value">{attach_html}</div>'
        "</div>\n"
        '<div class="detail-field">'
        '<div class="detail-label">Message ID</div>'
        f'<div class="detail-value"><code>{html.escape(record.message_id)}'
        "</code></div>"
        "</div>\n"
        f"{imap_uid_section}"
    )

    if embed:
        # Embedded (iframe) detail fragment — app stylesheet + fields.
        # The fragment is loaded as its own document inside the drawer
        # iframe, so it links the app stylesheet to stay styled.
        return (
            '<link rel="stylesheet" href="/static/automail/board.css">\n'
            '<div class="embed-detail">\n'
            f"{fields_html}"
            "</div>\n"
            "<script>\n"
            "if (window.parent && window.parent !== window\n"
            "    && typeof window.parent.refreshBoard === 'function') {\n"
            "  window.parent.refreshBoard(true);\n"
            "}\n"
            "</script>\n"
        )

    # Full standalone detail page.
    escaped_heading = html.escape(record.subject.strip() or "(no subject)")
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>Mail: {title_subject}</title>\n"
        '<link rel="stylesheet" href="/static/board.css">\n'
        '<link rel="stylesheet" href="/static/automail/board.css">\n'
        "</head>\n"
        "<body>\n"
        '<a class="back-link" href="/board">← Back to board</a>\n'
        '<div class="detail-container">\n'
        f"<h1>{escaped_heading}</h1>\n"
        f"{fields_html}"
        "</div>\n"
        "</body>\n"
        "</html>"
    )


def _render_move_form(
    record: MailRecord, current_action: str, redirect_input: str
) -> str:
    """Render the Status ``<option>`` list and the ``/move`` form."""
    options_parts: list[str] = []
    for action in TRIAGE_ACTION_ORDER:
        sel = " selected" if action == current_action else ""
        options_parts.append(
            f'<option value="{html.escape(action)}"{sel}>'
            f"{html.escape(TRIAGE_ACTION_LABELS[action])}</option>"
        )
    return (
        '<form class="detail-form" method="post" action="/move">'
        f'<input type="hidden" name="message_id"'
        f' value="{html.escape(record.message_id)}">'
        f"{redirect_input}"
        f'<select name="triage_action">{"".join(options_parts)}</select>'
        '<button type="submit">Move</button>'
        "</form>"
    )


def _render_body(record: MailRecord) -> tuple[str, str]:
    """Return ``(body_html_render, body_html_note)`` for a record's body."""
    body = _effective_body_plain(record)
    from_html = not record.body_plain.strip() and record.body_html.strip()
    if not body or not body.strip():
        body_html_render = '<span class="detail-value"><em>(no body)</em></span>'
    elif from_html:
        body_html_render = (
            f"<pre>{html.escape(body)}</pre>"
            "<span class='body-from-html'>(from HTML)</span>"
        )
    else:
        body_html_render = f"<pre>{html.escape(body)}</pre>"

    body_html_note = ""
    if record.body_html.strip():
        body_html_note = (
            '<div class="detail-field">'
            '<div class="detail-label">HTML version</div>'
            '<div class="detail-value"><em>HTML version available</em></div>'
            "</div>"
        )
    return body_html_render, body_html_note


def _render_notes_section(record: MailRecord, redirect_input: str) -> str:
    """Render the Notes textarea + ``/save-notes`` form."""
    escaped_notes = html.escape(record.notes)
    return (
        '<div class="detail-field">'
        '<div class="detail-label">Notes</div>'
        '<div class="detail-value">'
        '<form class="detail-form" method="post" action="/save-notes">'
        f'<input type="hidden" name="message_id"'
        f' value="{html.escape(record.message_id)}">'
        f"{redirect_input}"
        '<textarea class="detail-notes" name="notes" rows="4"'
        f' style="width:100%;box-sizing:border-box;">{escaped_notes}</textarea>'
        '<button type="submit">Save</button>'
        "</form>"
        "</div>"
        "</div>\n"
    )


def _render_draft_section(
    record: MailRecord,
    current_action: str,
    focus_draft: bool,
    redirect_input: str,
) -> str:
    """Render the Draft reply ``/save-draft`` form, or ``""`` when hidden.

    Visible when *current_action* is TO_ANSWER or DRAFT_READY, or when
    *focus_draft* is True (forced via ?draft=1).
    """
    if not (current_action in ("TO_ANSWER", "DRAFT_READY") or focus_draft):
        return ""
    escaped_draft = html.escape(record.draft_text)
    button_label = (
        "Update draft"
        if current_action == "DRAFT_READY"
        else "Save draft &amp; move to draft ready"
    )
    generate_label = (
        "Regenerate with AI" if current_action == "DRAFT_READY" else "Generate with AI"
    )
    generate_form = (
        '<form class="detail-form" method="post" action="/generate-draft">'
        f'<input type="hidden" name="message_id"'
        f' value="{html.escape(record.message_id)}">'
        f"{redirect_input}"
        f'<button type="submit" class="draft-reply-btn">{generate_label}</button>'
        "</form>"
    )
    # Only offer Reply / Reply-to-all once a draft has been saved
    # (DRAFT_READY).  Each form sends the saved draft via SMTP and then
    # archives the original message.
    send_forms = ""
    if current_action == "DRAFT_READY":
        send_forms = (
            '<form class="detail-form" method="post" action="/send-draft">'
            f'<input type="hidden" name="message_id"'
            f' value="{html.escape(record.message_id)}">'
            f"{redirect_input}"
            '<input type="hidden" name="reply_mode" value="reply">'
            '<button type="submit">Reply &amp; archive</button>'
            "</form>"
            '<form class="detail-form" method="post" action="/send-draft">'
            f'<input type="hidden" name="message_id"'
            f' value="{html.escape(record.message_id)}">'
            f"{redirect_input}"
            '<input type="hidden" name="reply_mode" value="reply_all">'
            '<button type="submit">Reply to all &amp; archive</button>'
            "</form>"
        )
    return (
        '<div class="detail-field">'
        '<div class="detail-label">Draft reply</div>'
        '<div class="detail-value">'
        f"{generate_form}"
        '<form class="detail-form" method="post" action="/save-draft">'
        f'<input type="hidden" name="message_id"'
        f' value="{html.escape(record.message_id)}">'
        f"{redirect_input}"
        '<textarea class="detail-draft" name="draft_text" rows="8"'
        f' style="width:100%;box-sizing:border-box;">{escaped_draft}</textarea>'
        f'<button type="submit">{button_label}</button>'
        "</form>"
        f"{send_forms}"
        "</div>"
        "</div>\n"
    )


def _render_recipients(to_list: list[str], cc_list: list[str]) -> tuple[str, str]:
    """Return ``(to_html, cc_section)`` for a record's recipients."""
    to_html = html.escape(", ".join(to_list)) if to_list else "<em>(none)</em>"
    cc_section = ""
    if cc_list:
        cc_html = html.escape(", ".join(cc_list))
        cc_section = (
            '<div class="detail-field">'
            '<div class="detail-label">CC</div>'
            f'<div class="detail-value">{cc_html}</div>'
            "</div>"
        )
    return to_html, cc_section


def _render_attachments(attachments: list[Any]) -> str:
    """Render the attachments summary string."""
    if attachments and isinstance(attachments, list) and len(attachments) > 0:
        attach_parts: list[str] = []
        for a in attachments:
            if isinstance(a, dict):
                fname = html.escape(str(a.get("filename", "?")))
                fsize = a.get("size")
                if fsize is not None and isinstance(fsize, (int, float)):
                    fsize_str = f" ({int(fsize):,} bytes)"
                else:
                    fsize_str = ""
                attach_parts.append(f"{fname}{fsize_str}")
            else:
                attach_parts.append(html.escape(str(a)))
        attach_html = ", ".join(attach_parts)
    else:
        attach_html = "<em>(none)</em>"
    return attach_html


def _render_imap_uid_section(record: MailRecord) -> str:
    """Render the IMAP UID field, or ``""`` when the record has no UID."""
    if record.imap_uid is None:
        return ""
    return (
        '<div class="detail-field">'
        '<div class="detail-label">IMAP UID</div>'
        f'<div class="detail-value"><code>{record.imap_uid}</code></div>'
        "</div>"
    )


def _render_triage_section(triage_decision: TriageDecision | None) -> str:
    """Render the read-only triage advisory field."""
    if triage_decision is not None:
        triage_value = (
            f"<strong>{html.escape(triage_decision.action)}</strong>"
            f' <span class="triage-source">'
            f"({html.escape(triage_decision.source)},"
            f" {html.escape(triage_decision.confidence)})</span>"
        )
        if triage_decision.reason:
            triage_value += (
                f'<div class="triage-reason">'
                f"{html.escape(triage_decision.reason)}</div>"
            )
    else:
        triage_value = "<em>(no triage decision)</em>"
    return (
        '<div class="detail-field">'
        '<div class="detail-label">Triage</div>'
        f'<div class="detail-value">{triage_value}</div>'
        "</div>\n"
    )
