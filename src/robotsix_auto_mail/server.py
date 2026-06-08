"""HTTP server for the read-only kanban mail board.

Provides ``make_board_handler`` — a factory that returns a
``BaseHTTPRequestHandler`` subclass wired to a specific SQLite database
path.
"""

from __future__ import annotations

import functools
import html
import json
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, quote, unquote

import jinja2

from robotsix_auto_mail.config import DEFAULT_ARCHIVE_ROOT, MailConfig
from robotsix_auto_mail.db import MailRecord, list_records
from robotsix_auto_mail.format import _BODY_PREVIEW_LIMIT, _format_date
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
    record_human_decision,
    set_archive_subfolder_override,
    set_rule_state,
    set_triage_decision,
)

# Jinja2 environment for the board/detail pages.  ``autoescape`` is enabled
# at the environment level (so the security default is safe), but each
# template wraps its body in ``{% autoescape false %}`` because every
# interpolated value is already escaped in Python via ``html.escape`` (which
# uses ``&quot;``/``&#x27;`` for quotes) — letting Jinja2 autoescape would
# both double-escape and switch to ``markupsafe`` quote sequences, changing
# the emitted bytes.
_JINJA_ENV = jinja2.Environment(autoescape=True)

_BOARD_COLUMNS = TRIAGE_ACTION_ORDER

_CSS = """\
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: system-ui, -apple-system, sans-serif;
  background: #e8e8e8; padding: 1.5rem;
}
h1 { margin-bottom: 1rem; font-size: 1.5rem; display: inline; }
#refresh-btn {
  display: inline; vertical-align: middle;
  margin-left: 0.5rem; margin-bottom: 1rem;
  background: none; border: 1px solid #ccc;
  border-radius: 4px; font-size: 1rem;
  cursor: pointer; padding: 0.1rem 0.4rem; color: #666;
}
#refresh-btn:hover { background: #eee; color: #000; }
#refresh-time {
  display: inline; vertical-align: middle;
  margin-left: 0.25rem; margin-bottom: 1rem;
  font-size: 0.75rem; color: #888;
}
.board { display: flex; gap: 1rem; overflow-x: auto; }
.column {
  flex: 1; min-width: 260px; background: #f5f5f5;
  border-radius: 8px; padding: 0.75rem;
}
.column-header {
  display: flex; justify-content: space-between;
  align-items: center; margin-bottom: 0.75rem;
  padding-bottom: 0.5rem; border-bottom: 2px solid #ddd;
}
.column-header h2 {
  font-size: 1rem; font-weight: 600;
}
.count {
  background: #666; color: #fff; font-size: 0.75rem;
  font-weight: 600; padding: 0.15rem 0.5rem; border-radius: 999px;
}
.cards { display: flex; flex-direction: column; gap: 0.5rem; }
.card {
  background: #fff; border: 1px solid #ddd;
  border-radius: 6px; padding: 0.6rem 0.75rem;
}
.card .sender {
  font-weight: 700; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap;
}
.card .subject { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card .date { font-size: 0.8rem; color: #888; }
.card .body-preview { font-size: 0.85rem; color: #444; margin-top: 0.25rem; }
.card .no-body { font-style: italic; color: #999; }
.card-form { margin-top: 0.4rem; display: flex; gap: 0.25rem; align-items: center; }
.card-form select { font-size: 0.75rem; padding: 0.1rem 0.2rem; }
.card-form button { font-size: 0.75rem; padding: 0.1rem 0.5rem; cursor: pointer; }
.card .subject a { color: inherit; text-decoration: none; }
.card .subject a:hover { text-decoration: underline; }

/* Rule proposals section */
.rule-proposals {
  background: #f5f5f5; border-radius: 8px;
  padding: 0.75rem; margin-bottom: 1rem;
}
.rule-proposals-header {
  display: flex; align-items: center; gap: 0.5rem;
  margin-bottom: 0.75rem; padding-bottom: 0.5rem;
  border-bottom: 2px solid #ddd;
}
.rule-proposals-header h2 { font-size: 1rem; font-weight: 600; }
.rule-cards { display: flex; flex-wrap: wrap; gap: 0.5rem; }
.rule-card {
  background: #fff; border: 1px solid #ddd;
  border-radius: 6px; padding: 0.6rem 0.75rem; min-width: 260px;
}
.rule-card .rule-title { font-weight: 700; }
.rule-card .rule-summary {
  font-size: 0.85rem; color: #444; margin-top: 0.25rem;
}
.rule-form { margin-top: 0.4rem; display: flex; gap: 0.25rem; }
.rule-form button { font-size: 0.75rem; padding: 0.1rem 0.5rem; cursor: pointer; }
.rule-empty { font-style: italic; color: #999; }

/* Detail page */
.back-link {
    display: inline-block; margin-bottom: 1rem;
    color: #333; text-decoration: none;
}
.back-link:hover { text-decoration: underline; }
.detail-container { max-width: 800px; }
.detail-field { margin-bottom: 0.75rem; }
.detail-label {
    font-weight: 700; font-size: 0.85rem;
    color: #666; margin-bottom: 0.15rem;
}
.detail-value { font-size: 0.95rem; }
.detail-value pre { margin: 0; white-space: pre-wrap; font-family: inherit; }
.detail-value code {
    font-size: 0.85rem; background: #eee;
    padding: 0.1rem 0.3rem; border-radius: 3px;
}
.detail-form { margin-top: 0.25rem; display: flex; gap: 0.25rem; align-items: center; }
.detail-form select { font-size: 0.8rem; padding: 0.15rem 0.3rem; }
.detail-form button { font-size: 0.8rem; padding: 0.15rem 0.6rem; cursor: pointer; }

/* Side panel */
.board-wrapper {
  transition: margin-right 0.3s ease;
}
.board-wrapper.panel-open {
  margin-right: 45vw;
}
.side-panel {
  position: fixed;
  top: 0; right: 0;
  width: 45vw; max-width: 100vw; height: 100vh;
  background: #fff;
  box-shadow: -2px 0 8px rgba(0,0,0,0.15);
  border-left: 1px solid #ddd;
  z-index: 1000;
  display: flex; flex-direction: column;
  transform: translateX(100%);
  transition: transform 0.3s ease;
  overflow-y: auto;
}
.side-panel.open {
  transform: translateX(0);
}
.panel-header {
  display: flex; justify-content: space-between;
  align-items: center;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #ddd;
  background: #f5f5f5;
}
.panel-header .panel-title {
  font-weight: 600; font-size: 0.95rem;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.panel-header .close-btn {
  background: none; border: none;
  font-size: 1.25rem; cursor: pointer;
  padding: 0 0.25rem; line-height: 1; color: #666;
}
.panel-header .close-btn:hover { color: #000; }
.side-panel iframe {
  flex: 1; border: none; width: 100%;
}
.delete-btn {
  background: #d32f2f; color: #fff; border: none;
  padding: 0.25rem 0.6rem; border-radius: 4px;
  cursor: pointer; font-size: 0.8rem;
  margin-top: 0.3rem;
}
.delete-btn:hover { background: #b71c1c; }

/* Triage-running indicator */
.triage-banner {
  background: #fff3cd; color: #664d03;
  text-align: center; padding: 0.5rem 1rem;
  border-radius: 6px; margin-bottom: 0.75rem;
  font-size: 0.9rem; border: 1px solid #ffecb5;
}

/* Archive proposal inline section */
.archive-proposal {
  margin-top: 0.35rem; font-size: 0.8rem;
  color: #555; display: flex; align-items: center;
  gap: 0.25rem; flex-wrap: wrap;
}
.archive-path { font-family: monospace; background: #eee;
  padding: 0.05rem 0.3rem; border-radius: 3px; }
.archive-exists { color: #2e7d32; font-weight: 600; font-size: 0.75rem; }
.archive-override-form { display: flex; gap: 0.15rem; align-items: center; }
.archive-override-form input[type="text"] {
  font-size: 0.7rem; padding: 0.1rem 0.2rem; border: 1px solid #ccc;
  border-radius: 3px; width: 160px;
}
.archive-override-form button {
  font-size: 0.7rem; padding: 0.1rem 0.4rem; cursor: pointer;
}
.archive-confirm-form { display: flex; gap: 0.15rem; align-items: center; }
.archive-btn {
  background: #2e7d32; color: #fff; border: none;
  padding: 0.25rem 0.6rem; border-radius: 4px;
  cursor: pointer; font-size: 0.75rem;
}
.archive-btn:hover { background: #1b5e20; }
"""


# Full ``/board`` document.  ``css`` and ``columns_html`` are passed in
# already-rendered (and, where applicable, ``html.escape``-d) so the template
# only assembles the surrounding chrome.  The body is wrapped in
# ``{% autoescape false %}`` to emit the pre-escaped values verbatim.
_BOARD_TEMPLATE = _JINJA_ENV.from_string(
    "{% autoescape false %}"
    "<!DOCTYPE html>\n"
    '<html lang="en">\n'
    "<head>\n"
    '<meta charset="utf-8">\n'
    "<title>Mail Board</title>\n"
    "<style>{{ css }}</style>\n"
    "</head>\n"
    "<body>\n"
    "<h1>Mail Board</h1>\n"
    '<button id="refresh-btn" title="Refresh now">↻</button>\n'
    '<span id="refresh-time"></span>\n'
    '{% if triage_running %}'
    '<div class="triage-banner">'
    'Triage is currently running. The board will refresh automatically.'
    '</div>\n'
    '<button type="submit" disabled'
    ' style="font-size:0.85rem; padding:0.25rem 0.75rem;'
    ' cursor:not-allowed; opacity:0.6;">'
    'Triage running\u2026</button>\n'
    '{% else %}'
    '<form method="post" action="/run-triage"'
    ' style="display:inline-block; margin-left:1.5rem; vertical-align:middle;">\n'
    '  <button type="submit"'
    ' style="font-size:0.85rem; padding:0.25rem 0.75rem; cursor:pointer;">'
    'Run triage</button>\n'
    '</form>\n'
    '{% endif %}'
    "{{ proposals_html }}\n"
    '<div class="board-wrapper">\n'
    '<div class="board">\n'
    "{{ columns_html }}"
    "\n</div>\n"
    "</div>\n"
    '<div class="side-panel" id="side-panel">\n'
    '<div class="panel-header">\n'
    '<span class="panel-title"></span>\n'
    '<button class="close-btn" onclick="closeDetail()">&times;</button>\n'
    "</div>\n"
    '<iframe src="" title="Mail detail"></iframe>\n'
    "</div>\n"
    "<script>\n"
    "function openDetail(messageId, subject) {\n"
    "  document.querySelector('.side-panel iframe').src"
    " = '/email/' + messageId + '?embed=1';\n"
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
    "  var card = e.target.closest('.card');\n"
    "  if (!card) return;\n"
    "  var mid = card.getAttribute('data-message-id');\n"
    "  if (!mid) return;\n"
    "  if (e.target.closest('form')) return;\n"
    "  e.preventDefault();\n"
    "  var subject = card.getAttribute('data-subject') || '';\n"
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
    "  if (document.getElementById('side-panel').classList.contains('open')) return;\n"
    "  fetch('/board-content')\n"
    "    .then(function(r) {\n"
    "      if (!r.ok) throw new Error('bad status');\n"
    "      return r.json();\n"
    "    })\n"
    "    .then(function(data) {\n"
    "      document.querySelector('.board').innerHTML = data.columns_html;\n"
    "      var proposals = document.querySelector('.rule-proposals');\n"
    "      if (proposals) proposals.outerHTML = data.proposals_html;\n"
    "      lastRefresh = Date.now();\n"
    "      updateRefreshTime();\n"
    "    })\n"
    "    .catch(function() { /* silently retry next cycle */ });\n"
    "}\n"
    "\n"
    "document.getElementById('refresh-btn').addEventListener('click', function() {\n"
    "  refreshBoard();\n"
    "  clearInterval(refreshTimer);\n"
    "  refreshTimer = setInterval(refreshBoard, 30000);\n"
    "});\n"
    "\n"
    "refreshTimer = setInterval(refreshBoard, 30000);\n"
    "refreshDisplayTimer = setInterval(updateRefreshTime, 10000);\n"
    "updateRefreshTime();\n"
    "</script>\n"
    "</body>\n"
    "</html>"
    "{% endautoescape %}"
)


# Embedded (iframe) detail fragment.  ``fields_html`` is the shared,
# already-escaped inner block.
_DETAIL_EMBED_TEMPLATE = _JINJA_ENV.from_string(
    "{% autoescape false %}"
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
    "{{ fields_html }}"
    "</div>\n"
    "{% endautoescape %}"
)


# Full standalone detail document.  ``title``/``heading`` are passed in
# already ``html.escape``-d; ``css`` and ``fields_html`` are trusted markup.
_DETAIL_PAGE_TEMPLATE = _JINJA_ENV.from_string(
    "{% autoescape false %}"
    "<!DOCTYPE html>\n"
    '<html lang="en">\n'
    "<head>\n"
    '<meta charset="utf-8">\n'
    "<title>Mail: {{ title }}</title>\n"
    "<style>{{ css }}</style>\n"
    "</head>\n"
    "<body>\n"
    '<a class="back-link" href="/board">← Back to board</a>\n'
    '<div class="detail-container">\n'
    "<h1>{{ heading }}</h1>\n"
    "{{ fields_html }}"
    "</div>\n"
    "</body>\n"
    "</html>"
    "{% endautoescape %}"
)


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


def _build_board_content(
    db_path: str, archive_root: str = DEFAULT_ARCHIVE_ROOT
) -> dict[str, str | bool]:
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

        columns: list[tuple[str, list[MailRecord]]] = [
            (action, column_buckets[action]) for action in _BOARD_COLUMNS
        ]
        # List (read-only) the pending deterministic-rule proposals so the
        # board can surface them for human validation.
        proposals = list_rule_proposals(conn, "pending")

        # -- archive proposal context -------------------------------------
        # Read the archive_structure watermark to know which folders exist.
        archive_raw = get_watermark(conn, "archive_structure")
        existing_folders: set[str] = set()
        if archive_raw is not None:
            try:
                existing_folders = set(json.loads(archive_raw))
            except (json.JSONDecodeError, TypeError):
                pass

        # Compute effective subfolder for each TO_ARCHIVE record.
        archive_subfolders: dict[str, str] = {}
        folder_exists: dict[str, bool] = {}
        for record in column_buckets.get("TO_ARCHIVE", []):
            subfolder = get_archive_subfolder(
                conn, record.message_id, record
            )
            archive_subfolders[record.message_id] = subfolder
            full_path = (
                f"{archive_root}/{subfolder}" if subfolder else archive_root
            )
            folder_exists[record.message_id] = full_path in existing_folders
    finally:
        conn.close()

    # Build column HTML fragments.
    columns_html_parts = [
        _render_column(
            action,
            records,
            triage_by_mid,
            archive_subfolders=archive_subfolders if action == "TO_ARCHIVE" else None,
            existing_folders=folder_exists if action == "TO_ARCHIVE" else None,
            archive_root=archive_root,
        )
        for action, records in columns
    ]

    return {
        "columns_html": "".join(columns_html_parts),
        "proposals_html": _render_rule_proposals(proposals),
        "triage_running": triage_running,
    }


def _build_board_html(
    db_path: str, archive_root: str = DEFAULT_ARCHIVE_ROOT
) -> str:
    """Build the full ``/board`` HTML document.

    Calls :func:`_build_board_content` and wraps the result in the
    full-page ``_BOARD_TEMPLATE``.  Raises ``Exception`` when the
    database cannot be opened (the caller should catch it and return
    a 503).
    """
    content = _build_board_content(db_path, archive_root=archive_root)
    return _BOARD_TEMPLATE.render(css=_CSS, **content)


def _build_detail_html(
    db_path: str,
    message_id: str,
    *,
    embed: bool = False,
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
    options_parts: list[str] = []
    for action in TRIAGE_ACTION_ORDER:
        sel = " selected" if action == current_action else ""
        options_parts.append(
            f'<option value="{html.escape(action)}"{sel}>'
            f"{html.escape(TRIAGE_ACTION_LABELS[action])}</option>"
        )

    quoted_mid = quote(record.message_id, safe="")
    redirect_input = ""
    if embed:
        redirect_input = (
            '<input type="hidden" name="redirect_to"'
            f' value="/email/{html.escape(quoted_mid)}?embed=1">'
        )
    move_form = (
        '<form class="detail-form" method="post" action="/move">'
        f'<input type="hidden" name="message_id"'
        f' value="{html.escape(record.message_id)}">'
        f"{redirect_input}"
        f'<select name="triage_action">{"".join(options_parts)}</select>'
        '<button type="submit">Move</button>'
        "</form>"
    )

    # Subject for title (truncated to ~60 chars)
    raw_subject = record.subject.strip() or "(no subject)"
    title_subject = raw_subject[:60] + ("…" if len(raw_subject) > 60 else "")

    # Date
    date_str = html.escape(_format_date(record.date))

    # Body plain
    body = record.body_plain
    if not body or not body.strip():
        body_html = '<span class="detail-value"><em>(no body)</em></span>'
    else:
        body_html = f"<pre>{html.escape(body)}</pre>"

    # Body HTML note
    body_html_note = ""
    if record.body_html.strip():
        body_html_note = (
            '<div class="detail-field">'
            '<div class="detail-label">HTML version</div>'
            '<div class="detail-value"><em>HTML version available</em></div>'
            "</div>"
        )

    # Recipients
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

    # Attachments
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

    # IMAP UID
    imap_uid_section = ""
    if record.imap_uid is not None:
        imap_uid_section = (
            '<div class="detail-field">'
            '<div class="detail-label">IMAP UID</div>'
            f'<div class="detail-value"><code>{record.imap_uid}</code></div>'
            "</div>"
        )

    # Triage decision (read-only advisory display).
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
    triage_section = (
        '<div class="detail-field">'
        '<div class="detail-label">Triage</div>'
        f'<div class="detail-value">{triage_value}</div>'
        "</div>\n"
    )

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
        f'<div class="detail-value">{body_html}</div>'
        "</div>\n"
        f"{body_html_note}"
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
        return _DETAIL_EMBED_TEMPLATE.render(fields_html=fields_html)

    return _DETAIL_PAGE_TEMPLATE.render(
        title=html.escape(title_subject),
        css=_CSS,
        heading=html.escape(record.subject.strip() or "(no subject)"),
        fields_html=fields_html,
    )


def _render_column(
    action: str,
    records: list[MailRecord],
    triage_by_mid: dict[str, TriageDecision],
    archive_subfolders: dict[str, str] | None = None,
    existing_folders: dict[str, bool] | None = None,
    archive_root: str = DEFAULT_ARCHIVE_ROOT,
) -> str:
    """Render a single board column (header + cards) as an HTML string."""
    title = TRIAGE_ACTION_LABELS[action]
    count = len(records)
    cards_html = "".join(
        _render_card(
            r,
            triage_by_mid.get(r.message_id),
            archive_subfolder=(
                archive_subfolders.get(r.message_id)
                if archive_subfolders is not None
                else None
            ),
            folder_exists=(
                existing_folders.get(r.message_id)
                if existing_folders is not None
                else None
            ),
            archive_root=archive_root,
        )
        for r in records
    )

    # Batch-delete button for the TO_DELETE column when it is non-empty.
    batch_delete_form = ""
    if action == "TO_DELETE" and records:
        batch_delete_form = (
            '<form class="delete-form" method="post" action="/batch-delete"'
            ' onsubmit="return confirm('
            "'Permanently delete ALL mail in this column"
            " from mailbox and database?')\">"
            '<button type="submit" class="delete-btn">Delete All</button>'
            "</form>"
        )

    return (
        f'<div class="column">'
        f'<div class="column-header"><h2>{html.escape(title)}</h2>'
        f'<span class="count">{count}</span>'
        f"{batch_delete_form}"
        f"</div>"
        f'<div class="cards">{cards_html}</div>'
        f"</div>"
    )


def _render_card(
    record: MailRecord,
    decision: TriageDecision | None = None,
    archive_subfolder: str | None = None,
    folder_exists: bool | None = None,
    archive_root: str = DEFAULT_ARCHIVE_ROOT,
) -> str:
    """Render a single ``MailRecord`` as a ``.card`` HTML string."""
    sender = html.escape(record.sender)
    subject = html.escape(record.subject) if record.subject.strip() else "(no subject)"
    subject_attr = html.escape(record.subject.strip() or "(no subject)")
    quoted_mid = quote(record.message_id, safe="")
    escaped_mid = html.escape(record.message_id)
    subject_html = f'<a href="/email/{quoted_mid}">{subject}</a>'
    date_str = html.escape(_format_date(record.date))

    body = record.body_plain
    if not body or not body.strip():
        body_html = '<span class="no-body">(no body)</span>'
    elif len(body) > _BODY_PREVIEW_LIMIT:
        body_html = html.escape(body[:_BODY_PREVIEW_LIMIT]) + "…"
    else:
        body_html = html.escape(body)

    # Determine which column this card is currently in based on its
    # triage decision (or "INBOX" when no decision exists).
    if decision is not None:
        current_action = decision.action
    else:
        current_action = "INBOX"

    # Build triage-action dropdown with the current column pre-selected.
    options_parts: list[str] = []
    for action in _BOARD_COLUMNS:
        sel = " selected" if action == current_action else ""
        options_parts.append(
            f'<option value="{html.escape(action)}"{sel}>'
            f"{html.escape(TRIAGE_ACTION_LABELS[action])}</option>"
        )

    form_html = (
        '<form class="card-form" method="post" action="/move">'
        f'<input type="hidden" name="message_id"'
        f' value="{escaped_mid}">'
        f'<select name="triage_action">{"".join(options_parts)}</select>'
        '<button type="submit">Move</button>'
        "</form>"
    )

    if current_action == "TO_DELETE":
        delete_form = (
            '<form class="delete-form" method="post" action="/delete"'
            ' onsubmit="return confirm('
            "'Permanently delete this mail from mailbox and database?')\">"
            f'<input type="hidden" name="message_id"'
            f' value="{escaped_mid}">'
            '<button type="submit" class="delete-btn">Delete</button>'
            "</form>"
        )
    else:
        delete_form = ""

    # -- archive proposal section (TO_ARCHIVE cards only) -------------------
    archive_html = ""
    if archive_subfolder is not None:
        escaped_subfolder = html.escape(archive_subfolder)
        escaped_root = html.escape(archive_root)
        if archive_subfolder:
            display_path = f"{escaped_root}/{escaped_subfolder}"
        else:
            display_path = escaped_root + "/"
        exists_indicator = ""
        if folder_exists:
            exists_indicator = (
                '<span class="archive-exists" title="Folder already exists">'
                "&#x2713;</span>"
            )
        archive_html = (
            '<div class="archive-proposal">'
            "Archive &rarr; "
            f'<span class="archive-path">{display_path}</span>'
            f"{exists_indicator}"
            '<form class="archive-override-form" method="post"'
            ' action="/archive-proposal">'
            f'<input type="hidden" name="message_id" value="{escaped_mid}">'
            '<input type="text" name="subfolder"'
            f' value="{escaped_subfolder}"'
            ' placeholder="subfolder path" size="30">'
            '<button type="submit">Set</button>'
            "</form>"
            '<form class="archive-confirm-form" method="post"'
            ' action="/archive"'
            ' onsubmit="return confirm('
            f"'Archive this mail to {display_path}?')\">"
            f'<input type="hidden" name="message_id" value="{escaped_mid}">'
            '<button type="submit" class="archive-btn">Archive</button>'
            "</form>"
            "</div>"
        )

    return (
        f'<div class="card" data-message-id="{quoted_mid}"'
        f' data-subject="{subject_attr}">'
        f'<div class="sender">{sender}</div>'
        f'<div class="subject">{subject_html}</div>'
        f'<div class="date">{date_str}</div>'
        f'<div class="body-preview">{body_html}</div>'
        f"{archive_html}"
        f"{form_html}"
        f"{delete_form}"
        f"</div>"
    )


def _render_rule_card(fingerprint: str, entry: RuleLedgerEntry) -> str:
    """Render one pending rule proposal as a ``.rule-card`` HTML string.

    Every interpolated value is passed through ``html.escape`` because the
    board templates run under ``{% autoescape false %}`` (see ``_JINJA_ENV``).
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


def _render_rule_proposals(
    proposals: list[tuple[str, RuleLedgerEntry]],
) -> str:
    """Render the "Rule proposals" board section as an HTML string.

    Shows one ``_render_rule_card`` per pending proposal, or an explicit
    empty-state message when there are none.  The count badge reuses the
    existing ``.count`` styling.
    """
    count = len(proposals)
    if proposals:
        cards_html = "".join(
            _render_rule_card(fingerprint, entry) for fingerprint, entry in proposals
        )
    else:
        cards_html = '<div class="rule-empty">No pending rule proposals</div>'
    return (
        '<div class="rule-proposals">'
        '<div class="rule-proposals-header">'
        "<h2>Rule proposals</h2>"
        f'<span class="count rule-count">{count}</span></div>'
        f'<div class="rule-cards">{cards_html}</div>'
        "</div>"
    )


def _run_triage_background(db_path: str) -> None:
    """Run the triage agent in a background thread, clearing the watermark on exit.

    Opens its own SQLite connection so it never shares a connection with
    the HTTP request-serve thread.  The ``triage_run:state`` watermark is
    always set back to ``"idle"`` in a ``finally`` block — even when the
    triage module cannot be imported or ``run_triage_agent`` raises.
    """
    from robotsix_auto_mail.db import init_db, set_watermark

    conn = init_db(db_path, skip_migrations=True)
    try:
        try:
            from robotsix_auto_mail.triage import run_triage_agent
        except ImportError:
            return
        run_triage_agent(conn)
    except Exception:  # noqa: S110  # nosec B110
        # Swallow all exceptions — the watermark is always cleared.
        pass
    finally:
        set_watermark(conn, "triage_run:state", "idle")
        conn.close()


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
            "/archive-proposal": self._handle_archive_proposal,
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
            payload = _build_board_content(
                self.db_path, archive_root=archive_root
            )
        except Exception:
            self._serve_json({"error": "Database unavailable"}, status=503)
            return

        self._serve_json(payload)

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
            if get_record_by_message_id(conn, message_id) is None:
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
                from robotsix_auto_mail.imap import ImapClient, ImapError

                try:
                    with ImapClient(self.mail_config) as client:
                        client.select_folder(
                            self.mail_config.imap_folder
                        )

                        # Determine the IMAP hierarchy delimiter.
                        existing = client.list_folders()
                        delimiter = next(
                            (f.delimiter for f in existing if f.delimiter),
                            "/",
                        )

                        # Build the destination IMAP folder name.
                        if subfolder:
                            translated = subfolder.replace("/", delimiter)
                            dest_folder = (
                                f"{effective_root}{delimiter}{translated}"
                            )
                        else:
                            dest_folder = effective_root

                        # -- security gate ---------------------------------
                        # Reject any destination that escapes the archive
                        # root (must start with root+delimiter or equal the
                        # root itself) and forbid ".." path segments.
                        root_prefix = f"{effective_root}{delimiter}"
                        if (
                            dest_folder != effective_root
                            and not dest_folder.startswith(root_prefix)
                        ):
                            self._bad_request(
                                "Archive destination escapes archive root"
                            )
                            return
                        if ".." in dest_folder.split(delimiter):
                            self._bad_request(
                                "Archive destination contains '..' "
                                "path segment"
                            )
                            return

                        # -- ensure destination folder hierarchy exists ----
                        parts = dest_folder.split(delimiter)
                        for i in range(1, len(parts) + 1):
                            client.create_folder(delimiter.join(parts[:i]))

                        client.move_message(
                            record.imap_uid, dest_folder
                        )
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
                d
                for d in list_triage_decisions(conn)
                if d.action == "TO_DELETE"
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
        message_id = unquote(path[len(prefix):])

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
            existing_folders: set[str] = set()
            if archive_raw is not None:
                try:
                    existing_folders = set(json.loads(archive_raw))
                except (json.JSONDecodeError, TypeError):
                    pass
            full_path = f"{archive_root}/{subfolder}" if subfolder else archive_root
            folder_exists = full_path in existing_folders
        finally:
            conn.close()

        self._serve_json({
            "subfolder": subfolder,
            "archive_root": archive_root,
            "folder_exists": folder_exists,
            "overridden": overridden,
            "source": source,
        })

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

        try:
            detail_html = _build_detail_html(
                self.db_path,
                message_id,
                embed=embed,
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
