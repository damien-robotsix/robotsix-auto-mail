"""HTTP server for the read-only kanban mail board.

Provides ``make_board_handler`` — a factory that returns a
``BaseHTTPRequestHandler`` subclass wired to a specific SQLite database
path.
"""

from __future__ import annotations

import functools
import html
import importlib.resources
import json
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler
from typing import Any, cast
from urllib.parse import parse_qs, quote, unquote

from robotsix_board import render_board

from robotsix_auto_mail.board_adapter import MailBoardAdapter
from robotsix_auto_mail.config import DEFAULT_ARCHIVE_ROOT, MailConfig
from robotsix_auto_mail.db import MailRecord, list_records
from robotsix_auto_mail.format import _format_date
from robotsix_auto_mail.triage import (
    TRIAGE_ACTION_LABELS,
    TRIAGE_ACTION_ORDER,
    VALID_TRIAGE_ACTIONS,
    RuleLedgerEntry,
    TriageDecision,
    TriageError,
    get_archive_subfolder,
    get_triage_decision,
    list_rule_proposals,
    list_triage_decisions,
    propose_archive_subfolder_llm,
    record_human_decision,
    set_archive_subfolder_override,
    set_rule_state,
    set_triage_decision,
)

# -- Static assets from robotsix_board -------------------------------------
# Pre-loaded at module level so _serve_static never touches the filesystem.
_STATIC_BOARD_JS = (
    importlib.resources.files("robotsix_board") / "static" / "board.js"
).read_text()
_STATIC_BOARD_CSS = (
    importlib.resources.files("robotsix_board") / "static" / "board.css"
).read_text()
# Auto-mail's app-layer stylesheet, served at /static/automail/board.css so
# it does not collide with the library's /static/board.css.  Loaded after
# the library CSS so its rules cascade over the library defaults.
_STATIC_AUTOMAIL_BOARD_CSS = (
    importlib.resources.files("robotsix_auto_mail") / "static" / "board.css"
).read_text()

# -- Constants --------------------------------------------------------------
_BOARD_COLUMNS = TRIAGE_ACTION_ORDER


def _is_safe_redirect_path(location: str) -> bool:
    """Return ``True`` if *location* is a safe same-origin relative path.

    Rejects values that could be used for open-redirect or HTTP
    response-splitting attacks.  A safe value must:

    - start with a single ``/`` (a relative, same-origin path),
    - not start with ``//`` (protocol-relative URL → other origin),
    - not start with ``/\\`` (backslash trick some browsers treat as
      protocol-relative), and
    - contain no CR (``\\r``), LF (``\\n``), or other ASCII control
      characters (which could inject extra response headers).
    """
    if not location.startswith("/"):
        return False
    if location.startswith(("//", "/\\")):
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in location):
        return False
    return True


class _NonEmptyColumnsAdapter:
    """Adapter view exposing only the populated columns to ``render_board``.

    auto-mail hides empty columns, but ``render_board`` renders one column
    per :meth:`MailBoardAdapter.columns` entry.  This thin wrapper scopes
    ``columns()`` to *status_keys* (the non-empty columns, in board order)
    and delegates every other attribute — the ``card_*`` scaffold methods,
    ``move_endpoint`` and the ``card_extra_html`` / ``column_extra_html``
    raw-HTML hooks — to the wrapped :class:`MailBoardAdapter`.
    """

    def __init__(self, adapter: MailBoardAdapter, status_keys: list[str]) -> None:
        self._adapter = adapter
        self._status_keys = status_keys

    def columns(self) -> list[tuple[str, str]]:
        labels = dict(self._adapter.columns())
        return [(key, labels[key]) for key in self._status_keys]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._adapter, name)


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


def _build_board_content(
    db_path: str, archive_root: str = DEFAULT_ARCHIVE_ROOT
) -> dict[str, str | bool | dict[str, dict[str, Any]]]:
    """Return ``{"columns_html": …, "proposals_html": …, "triage_running": …}``.

    Opens a fresh database connection, gathers every mail record and
    buckets them into kanban columns based on each record's triage
    decision.  Cards with no triage decision land in the ``"INBOX"``
    column.  Renders column and rule-proposal HTML fragments and
    returns them as a plain dict.

    Raises ``Exception`` when the database cannot be opened (the
    caller should catch it and return a 503).
    """
    from robotsix_auto_mail.db import get_watermark, init_db

    conn = init_db(db_path, skip_migrations=True)
    try:
        # Check whether the triage agent is currently running so the
        # board can show a visual indicator and disable the button.
        triage_running = get_watermark(conn, "triage_run:state") == "running"

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

        # List (read-only) the pending deterministic-rule proposals so the
        # board can surface them for human validation.
        proposals = list_rule_proposals(conn, "pending")

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

        # -- unsubscribe suggestions for TO_DELETE column -----------------
        suggestions_raw = get_watermark(conn, "unsubscribe_suggestions")
        unsubscribe_suggestions: dict[str, dict[str, Any]] = {}
        if suggestions_raw is not None:
            try:
                unsubscribe_suggestions = json.loads(suggestions_raw)
            except (json.JSONDecodeError, TypeError):
                pass

        # Build record_notes map for notes indicators.
        record_notes: dict[str, str] = {
            r.message_id: r.notes for r in all_records if r.notes
        }
    finally:
        conn.close()

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

    # -- rule proposals --------------------------------------------------------
    proposals_count = len(proposals)
    if proposals:
        rule_cards_html = "".join(
            _render_rule_card(fingerprint, entry) for fingerprint, entry in proposals
        )
    else:
        rule_cards_html = '<div class="rule-empty">No pending rule proposals</div>'
    proposals_html = (
        '<div class="rule-proposals">'
        '<div class="rule-proposals-header">'
        "<h2>Rule proposals</h2>"
        f'<span class="count rule-count">{proposals_count}</span></div>'
        f'<div class="rule-cards">{rule_cards_html}</div>'
        "</div>"
    )

    return {
        "columns_html": columns_html,
        "proposals_html": proposals_html,
        "triage_running": triage_running,
        "unsubscribe_suggestions": unsubscribe_suggestions,
    }


def _build_board_html(db_path: str, archive_root: str = DEFAULT_ARCHIVE_ROOT) -> str:
    """Build the full ``/board`` HTML document.

    Calls :func:`_build_board_content` and wraps the result in a minimal
    HTML5 page shell (Python f-strings, no Jinja2).  Raises ``Exception``
    when the database cannot be opened (the caller should catch it and
    return a 503).
    """
    content = _build_board_content(db_path, archive_root=archive_root)

    triage_control_html: str
    if content["triage_running"]:
        triage_control_html = (
            '<div class="triage-banner">'
            "Triage is currently running. The board will refresh automatically."
            "</div>\n"
            '<button type="submit" disabled'
            ' style="font-size:0.85rem; padding:0.25rem 0.75rem;'
            ' cursor:not-allowed; opacity:0.6;">'
            "Triage running\u2026</button>"
        )
    else:
        triage_control_html = (
            '<form method="post" action="/run-triage"'
            ' style="display:inline-block;'
            ' margin-left:1.5rem; vertical-align:middle;">\n'
            '  <button type="submit"'
            ' style="font-size:0.85rem; padding:0.25rem 0.75rem; cursor:pointer;">'
            "Run triage</button>\n"
            "</form>"
        )

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
        '<button id="refresh-btn" title="Refresh now">\u21bb</button>\n'
        '<span id="refresh-time"></span>\n'
        f'<span id="triage-control">{triage_control_html}</span>\n'
        f"{content['proposals_html']}\n"
        '<div class="board-wrapper">\n'
        '<div class="board">\n'
        f"{content['columns_html']}"
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
        "<script>\n"
        "function openDetail(messageId, subject, focusDraft) {\n"
        "  var src = '/email/' + messageId + '?embed=1';\n"
        "  if (focusDraft) src += '&draft=1';\n"
        "  document.querySelector('.side-panel iframe').src = src;\n"
        "  document.querySelector('.side-panel').classList.add('open');\n"
        "  document.querySelector('.board-wrapper').classList.add('panel-open');\n"
        "  document.querySelector('.panel-title').textContent = subject || '';\n"
        "  location.hash = messageId;\n"
        "}\n"
        "function closeDetail() {\n"
        "  document.querySelector('.side-panel').classList.remove('open');\n"
        "  document.querySelector('.board-wrapper').classList.remove('panel-open');\n"
        "  document.querySelector('.side-panel iframe').src = '';\n"
        "  location.hash = '';\n"
        "}\n"
        "if (location.hash) {\n"
        "  var mid = location.hash.slice(1);\n"
        "  if (mid) openDetail(mid);\n"
        "}\n"
        "window.addEventListener('hashchange', function() {\n"
        "  if (!location.hash) closeDetail();\n"
        "});\n"
        "window.addEventListener('keydown', function(e) {\n"
        "  if (e.key === 'Escape') closeDetail();\n"
        "});\n"
        "document.querySelector('.board').addEventListener('click', function(e) {\n"
        "  if (e.target.closest('button, select, input')) return;\n"
        "  var card = e.target.closest('.board-card');\n"
        "  if (!card) return;\n"
        "  var meta = card.querySelector('.card-extra');\n"
        "  var mid = meta && meta.getAttribute('data-message-id');\n"
        "  if (!mid) return;\n"
        "  if (e.target.closest('form')) return;\n"
        "  e.preventDefault();\n"
        "  var subject = (meta && meta.getAttribute('data-subject')) || '';\n"
        "  openDetail(mid, subject);\n"
        "});\n"
        "\n"
        "// Board auto-refresh polling\n"
        "var lastRefresh = Date.now();\n"
        "var refreshTimer = null;\n"
        "var refreshDisplayTimer = null;\n"
        "\n"
        "function relativeTime(ms) {\n"
        "  if (ms < 10000) return 'just now';\n"
        "  var sec = Math.floor(ms / 1000);\n"
        "  if (sec < 60) return sec + 's ago';\n"
        "  var min = Math.floor(sec / 60);\n"
        "  if (min < 60) return min + 'm ago';\n"
        "  return Math.floor(min / 60) + 'h ago';\n"
        "}\n"
        "\n"
        "function updateRefreshTime() {\n"
        "  var el = document.getElementById('refresh-time');\n"
        "  if (el) el.textContent = relativeTime(Date.now() - lastRefresh);\n"
        "}\n"
        "\n"
        "function refreshBoard() {\n"
        "  if (document.getElementById('side-panel')"
        ".classList.contains('open')) return;\n"
        "  fetch('/board-content')\n"
        "    .then(function(r) {\n"
        "      if (!r.ok) throw new Error('bad status');\n"
        "      return r.json();\n"
        "    })\n"
        "    .then(function(data) {\n"
        "      document.querySelector('.board').innerHTML = data.columns_html;\n"
        "      var proposals = document.querySelector('.rule-proposals');\n"
        "      if (proposals) proposals.outerHTML = data.proposals_html;\n"
        "      var tc = document.getElementById('triage-control');\n"
        "      if (tc) {\n"
        "        if (data.triage_running) {\n"
        '          tc.innerHTML = \'<div class="triage-banner">Triage is'
        " currently running. The board will refresh automatically.</div>'"
        '            + \'<button type="submit" disabled style="font-size:0.85rem;'
        ' padding:0.25rem 0.75rem; cursor:not-allowed; opacity:0.6;">Triage'
        " running\\u2026</button>';\n"
        "        } else {\n"
        '          tc.innerHTML = \'<form method="post" action="/run-triage"'
        ' style="display:inline-block; margin-left:1.5rem;'
        " vertical-align:middle;\\\">'"
        '            + \'<button type="submit" style="font-size:0.85rem;'
        " padding:0.25rem 0.75rem; cursor:pointer;\\\">Run triage</button></form>';\n"
        "        }\n"
        "      }\n"
        "      lastRefresh = Date.now();\n"
        "      updateRefreshTime();\n"
        "    })\n"
        "    .catch(function() { /* silently retry next cycle */ });\n"
        "}\n"
        "\n"
        "document.getElementById('refresh-btn')"
        ".addEventListener('click', function() {\n"
        "  refreshBoard();\n"
        "  clearInterval(refreshTimer);\n"
        "  refreshTimer = setInterval(refreshBoard, 30000);\n"
        "});\n"
        "\n"
        "refreshTimer = setInterval(refreshBoard, 30000);\n"
        "refreshDisplayTimer = setInterval(updateRefreshTime, 10000);\n"
        "updateRefreshTime();\n"
        "</script>\n"
        '<script src="/static/board.js"></script>\n'
        "</body>\n"
        "</html>"
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
    title_subject = raw_subject[:60] + ("\u2026" if len(raw_subject) > 60 else "")

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
        # Embedded (iframe) detail fragment — app stylesheet + inline
        # style + fields.  The fragment is loaded as its own document
        # inside the drawer iframe, so it links the app stylesheet to
        # stay styled.
        return (
            '<link rel="stylesheet" href="/static/automail/board.css">\n'
            "<style>\n"
            ".detail-field { margin-bottom: 0.75rem; }\n"
            ".detail-label { font-weight: 700; font-size: 0.85rem; color: #666;"
            " margin-bottom: 0.15rem; }\n"
            ".detail-value { font-size: 0.95rem; }\n"
            ".detail-value pre { margin: 0; white-space: pre-wrap;"
            " font-family: inherit; }\n"
            ".detail-value code { font-size: 0.85rem; background: #eee;"
            " padding: 0.1rem 0.3rem; border-radius: 3px; }\n"
            ".detail-form { margin-top: 0.25rem; display: flex; gap: 0.25rem;"
            " align-items: center; }\n"
            ".detail-form select { font-size: 0.8rem; padding: 0.15rem 0.3rem; }\n"
            ".detail-form button { font-size: 0.8rem; padding: 0.15rem 0.6rem;"
            " cursor: pointer; }\n"
            ".embed-detail { padding: 1rem;"
            " font-family: system-ui, -apple-system, sans-serif; }\n"
            "</style>\n"
            '<div class="embed-detail">\n'
            f"{fields_html}"
            "</div>\n"
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
        '<a class="back-link" href="/board">\u2190 Back to board</a>\n'
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
    body = record.body_plain
    if not body or not body.strip():
        body_html_render = '<span class="detail-value"><em>(no body)</em></span>'
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


def _render_rule_card(fingerprint: str, entry: RuleLedgerEntry) -> str:
    """Render one pending rule proposal as a ``.rule-card`` HTML string.

    Every interpolated value is passed through ``html.escape`` because the
    board pages use manual f-strings (no Jinja2 autoescape).
    """
    title = html.escape(entry.title)
    summary = html.escape(f"{entry.match_type}={entry.match_value} -> {entry.action}")
    fp = html.escape(fingerprint)
    return (
        '<div class="rule-card">'
        f'<div class="rule-title">{title}</div>'
        f'<div class="rule-summary">{summary}</div>'
        '<form class="rule-form" method="post" action="/rule-action">'
        f'<input type="hidden" name="fingerprint" value="{fp}">'
        '<button type="submit" name="decision" value="accept">Accept</button>'
        '<button type="submit" name="decision" value="reject">Reject</button>'
        "</form>"
        "</div>"
    )


def _run_triage_background(db_path: str) -> None:
    """Run the triage agent in a background thread, clearing the watermark on exit.

    Opens its own SQLite connection so it never shares a connection with
    the HTTP request-serve thread.  After triaging, derives fresh
    deterministic rule proposals from the updated triage history (no LLM)
    and records the genuinely-new ones as ``pending`` so the board can
    surface them for human validation.  The ``triage_run:state`` watermark
    is always set back to ``"idle"`` in a ``finally`` block — even when the
    triage module cannot be imported or ``run_triage_agent`` raises.
    """
    from robotsix_auto_mail.db import init_db, set_watermark

    conn = init_db(db_path, skip_migrations=True)
    try:
        try:
            from robotsix_auto_mail.triage import (
                propose_triage_rules,
                record_and_filter_rule_proposals,
                run_triage_agent,
            )
        except ImportError:
            return
        run_triage_agent(conn)
        # Surface freshly-derived rule proposals on the board.  This is a
        # deterministic, LLM-free scan of triage history, so it is cheap to
        # run on every triage pass; record_and_filter only writes the
        # ledger when there is a genuinely-new proposal.
        record_and_filter_rule_proposals(conn, propose_triage_rules(conn))
    except Exception:  # noqa: S110  # nosec B110
        # Swallow all exceptions — the watermark is always cleared.
        pass
    finally:
        set_watermark(conn, "triage_run:state", "idle")
        conn.close()


def _parse_archive_structure(
    raw: str | None, archive_root: str
) -> tuple[set[str], str, str]:
    """Parse the ``archive_structure`` watermark JSON.

    Returns ``(existing_folders, delimiter, effective_root)``.
    Falls back to ``(set(), "/", archive_root)`` when *raw* is None
    or cannot be parsed.
    """
    existing_folders: set[str] = set()
    delimiter: str = "/"
    effective_root: str = archive_root
    if raw is not None:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                # Old format: bare list of folder names.
                existing_folders = set(data)
                delimiter = "/"
                effective_root = data[0] if data else archive_root
            else:
                # New format: {"delimiter": ..., "folders": [...]}.
                existing_folders = set(data["folders"])
                delimiter = data.get("delimiter", "/")
                effective_root = data["folders"][0] if data["folders"] else archive_root
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
    return existing_folders, delimiter, effective_root


class BoardHandler(BaseHTTPRequestHandler):
    """Request handler for the robotsix-auto-mail board server.

    Routes ``GET /`` to a 301 redirect to ``/board``, ``GET /board`` to
    the kanban board HTML page, and everything else to 404.  The target
    SQLite database is injected per-instance via ``db_path``.
    """

    def __init__(
        self,
        *args: object,
        db_path: str,
        mail_config: MailConfig | None = None,
        **kwargs: object,
    ) -> None:
        # Set attributes BEFORE calling ``super().__init__`` because
        # ``BaseHTTPRequestHandler.__init__`` invokes ``handle()``
        # synchronously, which dispatches to ``do_GET``/``do_POST``.
        self.db_path = db_path
        self.mail_config = mail_config
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def do_GET(self) -> None:
        """Route GET requests via an ordered (predicate → handler) table."""
        routes: list[tuple[Callable[[str], bool], Callable[[], None]]] = [
            (lambda p: p == "/", lambda: self._redirect("/board")),
            (lambda p: p == "/board", self._serve_board),
            (lambda p: p == "/board-content", self._serve_board_content),
            (lambda p: p.startswith("/static/"), self._serve_static),
            (
                lambda p: p.startswith("/email/") and p.endswith("/status"),
                self._serve_email_status,
            ),
            (lambda p: p.startswith("/email/"), self._serve_email_detail),
            (
                lambda p: p.startswith("/archive-proposal/"),
                self._serve_archive_proposal,
            ),
        ]
        for matches, handler in routes:
            if matches(self.path):
                handler()
                return
        self._not_found()

    def do_POST(self) -> None:
        """Route POST requests via an exact-match table."""
        # Periodic-trigger decision — Option A (on-demand endpoint
        # only): no background/periodic runner is added.  The
        # deterministic ``check_config_sync.py`` remains the fast, free,
        # blocking CI gate; the LLM agent is an optional advisory tool,
        # so it does not need to run on a schedule.  The board server is
        # a single-threaded ``BaseHTTPRequestHandler``/``HTTPServer``
        # with no scheduler — adding a ``while True``/``time.sleep`` loop
        # would block request serving and is out of scope.  External
        # schedulers (cron, systemd timer) can simply POST to
        # ``/config-sync``, which fully satisfies optional periodic
        # invocation without new in-process machinery.  Option B (an
        # in-process periodic runner) is explicitly deferred.
        routes: dict[str, Callable[[], None]] = {
            "/move": self._handle_move,
            "/delete": self._handle_delete,
            "/archive": self._handle_archive,
            "/batch-delete": self._handle_batch_delete,
            "/rule-action": self._handle_rule_action,
            "/config-sync": self._handle_config_sync,
            "/run-triage": self._handle_run_triage,
            "/force-triage-column": self._handle_force_triage_column,
            "/archive-proposal": self._handle_archive_proposal,
            "/save-notes": self._handle_save_notes,
            "/save-draft": self._handle_save_draft,
            "/generate-draft": self._handle_generate_draft,
        }
        handler = routes.get(self.path)
        if handler is None:
            self._not_found()
            return
        handler()

    def _send_response(
        self,
        body: bytes | str,
        status: int = 200,
        content_type: str = "text/plain; charset=utf-8",
    ) -> None:
        """Write a complete response (status line, headers, body).

        The single place that writes response headers + body — all
        handler methods delegate here (the only other writer is
        ``_redirect``, which emits a bodiless ``Location`` redirect).
        """
        encoded = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _redirect(self, location: str, code: int = 301) -> None:
        """Send a redirect to *location*.

        Defense-in-depth at the sink: if *location* carries any CR/LF
        or other ASCII control character (which could split the HTTP
        response and inject extra headers), fall back to ``/board`` so
        the ``Location`` header can never carry such a value.
        """
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in location):
            location = "/board"
        self.send_response(code)
        self.send_header("Location", location)
        self.end_headers()

    def _not_found(self) -> None:
        """Send a 404 Not Found."""
        self._send_response(b"Not found", status=404)

    def _bad_request(self, message: str) -> None:
        """Send a 400 Bad Request with a plain-text body."""
        self._send_response(message, status=400)

    def _serve_json(self, payload: Mapping[str, object], status: int = 200) -> None:
        """Serialize *payload* as JSON and send it with *status*."""
        self._send_response(
            json.dumps(payload),
            status=status,
            content_type="application/json; charset=utf-8",
        )

    def _serve_board(self) -> None:
        """Render and serve the kanban board HTML."""
        archive_root = (
            self.mail_config.archive_root
            if self.mail_config is not None
            else DEFAULT_ARCHIVE_ROOT
        )
        try:
            body = _build_board_html(self.db_path, archive_root=archive_root)
        except Exception:
            self._send_response("Database unavailable", status=503)
            return

        self._send_response(body, content_type="text/html; charset=utf-8")

    def _serve_board_content(self) -> None:
        """Render and serve the board content as JSON."""
        archive_root = (
            self.mail_config.archive_root
            if self.mail_config is not None
            else DEFAULT_ARCHIVE_ROOT
        )
        try:
            payload = _build_board_content(self.db_path, archive_root=archive_root)
        except Exception:
            self._serve_json({"error": "Database unavailable"}, status=503)
            return

        self._serve_json(payload)

    def _serve_static(self) -> None:
        """Serve static assets from the robotsix_board package."""
        if self.path == "/static/board.js":
            self._send_response(
                _STATIC_BOARD_JS,
                content_type="text/javascript; charset=utf-8",
            )
        elif self.path == "/static/board.css":
            self._send_response(
                _STATIC_BOARD_CSS,
                content_type="text/css; charset=utf-8",
            )
        elif self.path == "/static/automail/board.css":
            self._send_response(
                _STATIC_AUTOMAIL_BOARD_CSS,
                content_type="text/css; charset=utf-8",
            )
        else:
            self._not_found()

    def _handle_move(self) -> None:
        """Process POST /move — update a card's triage decision and redirect."""
        from robotsix_auto_mail.db import get_record_by_message_id, init_db

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        # parse_qs returns {key: [value, ...]} — extract first value.
        message_id = (fields.get("message_id") or [""])[0].strip()
        triage_action = (fields.get("triage_action") or [""])[0].strip()
        redirect_to = (fields.get("redirect_to") or [""])[0].strip()

        if not message_id or not triage_action:
            self._bad_request("Missing message_id or triage_action")
            return

        if triage_action not in VALID_TRIAGE_ACTIONS:
            self._bad_request(f"Invalid triage action: {triage_action!r}")
            return

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            # Verify the record exists before upserting a triage decision
            # (foreign key would reject it anyway, but we want a clean 404).
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                self._not_found()
                return
            set_triage_decision(
                conn,
                message_id,
                triage_action,
                source="user",
                reason=f"moved to {triage_action}",
            )
            record_human_decision(conn, message_id, triage_action)

            if triage_action == "TO_ARCHIVE":
                try:
                    if record is not None and self.mail_config is not None:
                        propose_archive_subfolder_llm(
                            conn, record, self.mail_config.llm_api_key
                        )
                except Exception:  # noqa: S110  # nosec B110
                    pass  # Non-fatal: board falls back to deterministic proposal
        finally:
            conn.close()

        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, code=302)
        else:
            self._redirect("/board", code=302)

    def _handle_delete(self) -> None:
        """Process POST /delete — delete mail from IMAP mailbox and local DB."""
        from robotsix_auto_mail.db import (
            delete_record_by_message_id,
            get_record_by_message_id,
            init_db,
        )

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()
        redirect_to = (fields.get("redirect_to") or [""])[0].strip()

        if not message_id:
            self._bad_request("Missing message_id")
            return

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                self._not_found()
                return

            # -- IMAP deletion (when config and UID are both available) --
            if self.mail_config is not None and record.imap_uid is not None:
                from robotsix_auto_mail.imap import ImapClient, ImapError

                try:
                    with ImapClient(self.mail_config) as client:
                        client.select_folder(self.mail_config.imap_folder)
                        client.delete_message(record.imap_uid)
                except (ImapError, OSError) as exc:
                    self._send_response(
                        f"IMAP deletion failed: {exc}",
                        status=502,
                    )
                    return

            # -- local DB deletion --
            delete_record_by_message_id(conn, message_id)
        finally:
            conn.close()

        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, code=302)
        else:
            self._redirect("/board", code=302)

    def _imap_archive_move(
        self,
        mail_config: MailConfig,
        imap_uid: int,
        effective_root: str,
        subfolder: str | None,
    ) -> None:
        """Move a message to the archive folder via IMAP.

        Raises ValueError on security-policy violations (caller should
        return 400).  Raises ImapError or OSError on IMAP/IO failures
        (caller should return 502).
        """
        from robotsix_auto_mail.imap import ImapClient

        with ImapClient(mail_config) as client:
            client.select_folder(mail_config.imap_folder)

            # Determine the IMAP hierarchy delimiter.
            existing = client.list_folders()
            delimiter = next(
                (f.delimiter for f in existing if f.delimiter),
                "/",
            )

            # Build the destination IMAP folder name.
            if subfolder:
                translated = subfolder.replace("/", delimiter)
                dest_folder = f"{effective_root}{delimiter}{translated}"
            else:
                dest_folder = effective_root

            # -- security gate ---------------------------------
            # Reject any destination that escapes the archive
            # root (must start with root+delimiter or equal the
            # root itself) and forbid ".." path segments.
            root_prefix = f"{effective_root}{delimiter}"
            if dest_folder != effective_root and not dest_folder.startswith(
                root_prefix
            ):
                raise ValueError("Archive destination escapes archive root")
            if ".." in dest_folder.split(delimiter):
                raise ValueError("Archive destination contains '..' path segment")

            # -- ensure destination folder hierarchy exists ----
            parts = dest_folder.split(delimiter)
            for i in range(1, len(parts) + 1):
                client.create_folder(delimiter.join(parts[:i]))

            client.move_message(imap_uid, dest_folder)

    def _handle_archive(self) -> None:
        """Process POST /archive — move mail to archive folder via IMAP
        and remove it from the local database.
        """
        from robotsix_auto_mail.db import (
            delete_record_by_message_id,
            get_record_by_message_id,
            init_db,
        )

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()

        if not message_id:
            self._bad_request("Missing message_id")
            return

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                self._not_found()
                return

            # Compute the effective archive subfolder.
            subfolder = get_archive_subfolder(conn, message_id, record)

            # Determine the archive root.
            archive_root = (
                self.mail_config.archive_root
                if self.mail_config is not None
                else DEFAULT_ARCHIVE_ROOT
            )

            # Determine the namespace prefix (empty when unset).
            namespace = (
                self.mail_config.archive_namespace
                if self.mail_config is not None
                else ""
            )

            # Effective root: namespace + archive_root (user supplies
            # the delimiter as part of the namespace, e.g. "INBOX.").
            effective_root = namespace + archive_root

            # -- IMAP move phase (only when IMAP is configured and the
            #    record has a tracked UID) --
            if self.mail_config is not None and record.imap_uid is not None:
                from robotsix_auto_mail.imap import ImapError

                try:
                    self._imap_archive_move(
                        self.mail_config,
                        record.imap_uid,
                        effective_root,
                        subfolder,
                    )
                except ValueError as exc:
                    self._bad_request(str(exc))
                    return
                except (ImapError, OSError) as exc:
                    self._send_response(
                        f"IMAP archive failed: {exc}",
                        status=502,
                    )
                    return

            # -- local DB cleanup --
            delete_record_by_message_id(conn, message_id)
        finally:
            conn.close()

        self._redirect("/board", code=302)

    def _handle_batch_delete(self) -> None:
        """Process POST /batch-delete — delete all TO_DELETE mail from IMAP
        and local DB in a single request.

        All-or-nothing guard: if any IMAP deletion fails the handler
        returns 502 and **no** local database changes are made.
        """
        from robotsix_auto_mail.db import (
            delete_record_by_message_id,
            get_record_by_message_id,
            init_db,
        )

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            # Collect every TO_DELETE decision and its MailRecord.
            to_delete_decisions = [
                d for d in list_triage_decisions(conn) if d.action == "TO_DELETE"
            ]
            records: list[MailRecord] = []
            for decision in to_delete_decisions:
                record = get_record_by_message_id(conn, decision.message_id)
                if record is not None:
                    records.append(record)

            # -- IMAP deletion phase (single connection) ------------------
            if self.mail_config is not None and any(
                r.imap_uid is not None for r in records
            ):
                from robotsix_auto_mail.imap import ImapClient, ImapError

                try:
                    with ImapClient(self.mail_config) as client:
                        client.select_folder(self.mail_config.imap_folder)
                        for record in records:
                            if record.imap_uid is not None:
                                client.delete_message(record.imap_uid)
                except (ImapError, OSError) as exc:
                    self._send_response(
                        f"IMAP deletion failed: {exc}",
                        status=502,
                    )
                    return

            # -- local DB deletion phase ----------------------------------
            for record in records:
                delete_record_by_message_id(conn, record.message_id)
        finally:
            conn.close()

        self._redirect("/board", code=302)

    def _handle_rule_action(self) -> None:
        """Process POST /rule-action — accept/reject a rule proposal."""
        from robotsix_auto_mail.db import init_db

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        # parse_qs returns {key: [value, ...]} — extract first value.
        fingerprint = (fields.get("fingerprint") or [""])[0].strip()
        decision = (fields.get("decision") or [""])[0].strip()

        if not fingerprint or not decision:
            self._bad_request("Missing fingerprint or decision")
            return

        decision_to_state = {"accept": "accepted", "reject": "rejected"}
        mapped_state = decision_to_state.get(decision)
        if mapped_state is None:
            self._bad_request(f"Invalid decision: {decision!r}")
            return

        conn = init_db(self.db_path)
        try:
            set_rule_state(conn, fingerprint, mapped_state)
        except TriageError:
            self._not_found()
            return
        finally:
            conn.close()

        self._redirect("/board", code=302)

    def _handle_config_sync(self) -> None:
        """Process POST /config-sync — run the LLM drift advisory agent.

        Lazily imports the optional LLM-backed agent so the rest of the
        server works without ``pydantic_ai`` installed.  On success,
        returns the ``ConfigSyncResult`` serialized as JSON; on a missing
        optional extra returns 503, and on any agent failure returns 503
        with a JSON error body (never a traceback).
        """
        try:
            from robotsix_auto_mail.config.config_sync_agent import (
                ConfigSyncError,
                run_config_sync_agent,
            )
        except ImportError:
            self._serve_json(
                {
                    "error": (
                        "Config-sync advisory requires the optional LLM "
                        "extra, which is not installed"
                    )
                },
                status=503,
            )
            return

        from robotsix_auto_mail.db import init_db

        conn = init_db(self.db_path)
        try:
            result = run_config_sync_agent(conn=conn)
        except ConfigSyncError as exc:
            self._serve_json({"error": str(exc)}, status=503)
            return
        except Exception as exc:
            self._serve_json({"error": str(exc)}, status=503)
            return
        finally:
            conn.close()

        self._serve_json(result.model_dump(), status=200)

    def _handle_run_triage(self) -> None:
        """Process POST /run-triage — launch triage agent in a background thread.

        Idempotent: if triage is already running the request is a no-op
        that redirects to ``/board`` immediately.  Otherwise a watermark
        is set and a daemon thread is spawned to run the agent; the
        thread clears the watermark in a ``finally`` block so the board
        always recovers.
        """
        import threading

        from robotsix_auto_mail.db import get_watermark, init_db, set_watermark

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            if get_watermark(conn, "triage_run:state") == "running":
                self._redirect("/board", code=302)
                return
            set_watermark(conn, "triage_run:state", "running")
        finally:
            conn.close()

        threading.Thread(
            target=_run_triage_background,
            args=(self.db_path,),
            daemon=True,
        ).start()

        self._redirect("/board", code=302)

    def _handle_force_triage_column(self) -> None:
        """Process POST /force-triage-column — reset triage decisions for
        one column, then launch the triage agent in a background thread.

        Follows the same pattern as :meth:`_handle_run_triage`: decisions
        are deleted, then the global agent is spawned (or joined if
        already running).  The watermark guard ensures only one triage
        run is in flight at a time.
        """
        import threading
        import urllib.parse

        from robotsix_auto_mail.db import (
            VALID_TRIAGE_ACTIONS,
            get_watermark,
            init_db,
            set_watermark,
        )
        from robotsix_auto_mail.triage import (
            TriageError,
            delete_triage_decisions_by_action,
        )

        # -- parse body ---------------------------------------------------
        content_length = int(self.headers.get("content-length", "0"))
        raw_body = self.rfile.read(content_length) if content_length else b""
        params = urllib.parse.parse_qs(raw_body.decode("utf-8"))
        action_list = params.get("action", [])
        if not action_list or not action_list[0].strip():
            self._bad_request("Missing 'action' parameter")
            return
        action = action_list[0].strip()
        if action not in VALID_TRIAGE_ACTIONS:
            self._bad_request(f"Invalid triage action: {action!r}")
            return

        # -- clear decisions ----------------------------------------------
        try:
            conn = init_db(self.db_path, skip_migrations=True)
            try:
                delete_triage_decisions_by_action(conn, action)
            finally:
                conn.close()
        except TriageError as exc:
            self._bad_request(str(exc))
            return
        except Exception as exc:
            self._send_response(
                json.dumps({"error": str(exc)}).encode(),
                status=503,
                content_type="application/json",
            )
            return

        # -- launch triage (same pattern as _handle_run_triage) -----------
        conn = init_db(self.db_path, skip_migrations=True)
        try:
            if get_watermark(conn, "triage_run:state") == "running":
                self._redirect("/board", code=302)
                return
            set_watermark(conn, "triage_run:state", "running")
        finally:
            conn.close()

        threading.Thread(
            target=_run_triage_background,
            args=(self.db_path,),
            daemon=True,
        ).start()

        self._redirect("/board", code=302)

    def _serve_archive_proposal(self) -> None:
        """Serve GET /archive-proposal/{message_id} — return JSON with
        effective subfolder, source, and folder-exists status."""
        from robotsix_auto_mail.db import (
            get_record_by_message_id,
            get_watermark,
            init_db,
        )
        from robotsix_auto_mail.triage import (
            _load_archive_overrides,
            _load_llm_archive_hints,
        )

        path = self.path
        prefix = "/archive-proposal/"
        message_id = unquote(path[len(prefix) :])

        archive_root = (
            self.mail_config.archive_root
            if self.mail_config is not None
            else DEFAULT_ARCHIVE_ROOT
        )

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                self._not_found()
                return

            subfolder = get_archive_subfolder(conn, message_id, record)
            overrides = _load_archive_overrides(conn)
            hints = _load_llm_archive_hints(conn)

            if message_id in overrides:
                source = "override"
                overridden = True
            elif message_id in hints:
                source = "llm"
                overridden = False
            else:
                source = "rule"
                overridden = False

            # Determine folder_exists
            archive_raw = get_watermark(conn, "archive_structure")
            existing_folders, delimiter, effective_root = _parse_archive_structure(
                archive_raw, archive_root
            )
            if subfolder:
                translated = subfolder.replace("/", delimiter)
                full_path = f"{effective_root}{delimiter}{translated}"
            else:
                full_path = effective_root
            folder_exists = full_path in existing_folders
        finally:
            conn.close()

        self._serve_json(
            {
                "subfolder": subfolder,
                "archive_root": archive_root,
                "folder_exists": folder_exists,
                "overridden": overridden,
                "source": source,
            }
        )

    def _handle_archive_proposal(self) -> None:
        """Process POST /archive-proposal — store a user override and redirect."""
        from robotsix_auto_mail.db import init_db

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()
        subfolder = (fields.get("subfolder") or [""])[0].strip()

        if not message_id:
            self._bad_request("Missing message_id")
            return

        if subfolder:
            if subfolder.startswith("/"):
                self._bad_request("Subfolder must not be an absolute path")
                return
            if any(segment == ".." for segment in subfolder.split("/")):
                self._bad_request("Subfolder must not contain '..' segments")
                return
            if len(subfolder) > 256:
                self._bad_request("Subfolder exceeds maximum length of 256 characters")
                return

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            set_archive_subfolder_override(conn, message_id, subfolder)
        finally:
            conn.close()

        self._redirect("/board", code=302)

    def _handle_save_notes(self) -> None:
        """Process POST /save-notes — persist notes for a mail record."""
        from robotsix_auto_mail.db import (
            get_record_by_message_id,
            init_db,
            update_notes,
        )

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()
        notes = (fields.get("notes") or [""])[0]
        redirect_to = (fields.get("redirect_to") or [""])[0].strip()

        if not message_id:
            self._bad_request("Missing message_id")
            return

        # Verify the record exists (read-only check).
        conn = init_db(self.db_path, skip_migrations=True)
        try:
            if get_record_by_message_id(conn, message_id) is None:
                self._not_found()
                return
        finally:
            conn.close()

        # Persist the notes.
        conn = init_db(self.db_path)
        try:
            update_notes(conn, message_id, notes)
        finally:
            conn.close()

        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, code=302)
        else:
            self._redirect("/board", code=302)

    def _handle_save_draft(self) -> None:
        """Process POST /save-draft — persist draft text and move to DRAFT_READY."""
        from robotsix_auto_mail.db import (
            get_record_by_message_id,
            init_db,
            update_draft_text,
        )

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()
        draft_text = (fields.get("draft_text") or [""])[0]
        redirect_to = (fields.get("redirect_to") or [""])[0].strip()

        if not message_id:
            self._bad_request("Missing message_id")
            return

        # Verify the record exists (read-only check).
        conn = init_db(self.db_path, skip_migrations=True)
        try:
            if get_record_by_message_id(conn, message_id) is None:
                self._not_found()
                return
        finally:
            conn.close()

        # Persist draft text and move to DRAFT_READY.
        conn = init_db(self.db_path)
        try:
            update_draft_text(conn, message_id, draft_text)

            current = get_triage_decision(conn, message_id)
            if current is None or current.action != "DRAFT_READY":
                set_triage_decision(
                    conn,
                    message_id,
                    "DRAFT_READY",
                    source="user",
                    reason="draft saved",
                )
                record_human_decision(conn, message_id, "DRAFT_READY")
        finally:
            conn.close()

        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, code=302)
        else:
            self._redirect("/board", code=302)

    def _handle_generate_draft(self) -> None:
        """Process POST /generate-draft — LLM-generate a draft reply.

        Lazily imports the optional LLM-backed draft generator so the rest
        of the server works without ``pydantic_ai`` installed.  On a missing
        optional extra (``ImportError``) the handler degrades gracefully by
        redirecting back to the detail/board view (the manual textarea stays
        usable) rather than returning a 503 — a full-page POST cannot render
        a clean JSON error.  Generation failures are likewise swallowed so
        the existing draft/manual form remains available.
        """
        from robotsix_auto_mail.db import init_db

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()
        redirect_to = (fields.get("redirect_to") or [""])[0].strip()

        if not message_id:
            self._bad_request("Missing message_id")
            return

        try:
            from robotsix_auto_mail.draft import (
                DraftGenerationError,
                generate_draft_reply,
            )
        except ImportError:
            # Optional LLM extra not installed — degrade silently.
            self._redirect_generate_draft(message_id, redirect_to)
            return

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            try:
                generate_draft_reply(
                    conn,
                    message_id,
                    api_key=(
                        self.mail_config.llm_api_key if self.mail_config else None
                    ),
                )
            except DraftGenerationError:
                # Generation failed — degrade gracefully (existing draft /
                # manual form stays); fall through to the redirect.
                pass
            else:
                set_triage_decision(
                    conn,
                    message_id,
                    "DRAFT_READY",
                    source="user",
                    reason="draft generated",
                )
        finally:
            conn.close()

        self._redirect_generate_draft(message_id, redirect_to)

    def _redirect_generate_draft(self, message_id: str, redirect_to: str) -> None:
        """Redirect after /generate-draft to *redirect_to* or the board panel.

        When *redirect_to* is a safe relative path it is used (returning the
        iframe to the embed detail view).  Otherwise a server-side trusted
        ``/board#{message_id}`` redirect re-opens the side panel on the now
        ``DRAFT_READY`` card.
        """
        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, 302)
        else:
            self._redirect(f"/board#{quote(message_id)}", 302)

    def _serve_email_status(self) -> None:
        """Serve GET /email/{message_id}/status — return triage action as text.

        Returns ``"INBOX"`` when the record exists but has no triage
        decision.  Returns 404 when the message_id is unknown.
        """
        from robotsix_auto_mail.db import get_record_by_message_id, init_db

        # Extract the URL-encoded message_id from the path:
        #   "/email/<encoded>/status" → extract and decode.
        path = self.path
        prefix = "/email/"
        suffix = "/status"
        encoded_mid = path[len(prefix) : -len(suffix)]
        message_id = unquote(encoded_mid)

        conn = init_db(self.db_path)
        try:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                self._not_found()
                return
            decision = get_triage_decision(conn, message_id)
        finally:
            conn.close()

        if decision is None:
            self._send_response("INBOX")
            return

        self._send_response(decision.action)

    def _serve_email_detail(self) -> None:
        """Serve GET /email/{message_id} — full detail page.

        Supports ``?embed=1`` to return a fragment suitable for an
        iframe (no full-page chrome, no refresh).
        """
        from urllib.parse import parse_qs, urlparse

        path = self.path
        prefix = "/email/"

        # Separate path from query string.
        parsed = urlparse(path)
        message_id = unquote(parsed.path[len(prefix) :])
        qs = parse_qs(parsed.query)
        embed = qs.get("embed", ["0"])[0] == "1"
        focus_draft = qs.get("draft", ["0"])[0] == "1"

        try:
            detail_html = _build_detail_html(
                self.db_path,
                message_id,
                embed=embed,
                focus_draft=focus_draft,
            )
        except Exception:
            self._send_response("Database unavailable", status=503)
            return

        if detail_html is None:
            self._not_found()
            return

        self._send_response(detail_html, content_type="text/html; charset=utf-8")

    def log_message(self, format: str, *args: object) -> None:
        """Suppress logging to stderr (keep server quiet)."""
        pass


def make_board_handler(
    db_path: str,
    mail_config: MailConfig | None = None,
) -> functools.partial[BoardHandler]:
    """Return a callable that builds a ``BoardHandler`` wired to *db_path*.

    ``HTTPServer`` calls the result as ``handler(request, client_address,
    server)``; the returned ``functools.partial`` binds *db_path* and
    *mail_config* as keyword arguments so the standard three positional
    args still flow through to ``BoardHandler.__init__``.
    """
    return functools.partial(
        BoardHandler,
        db_path=db_path,
        mail_config=mail_config,
    )
