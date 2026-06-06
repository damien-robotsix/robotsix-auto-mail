"""HTTP server for the read-only kanban mail board.

Provides ``make_board_handler`` — a factory that returns a
``BaseHTTPRequestHandler`` subclass wired to a specific SQLite database
path.
"""

from __future__ import annotations

import functools
import html
import json
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, quote, unquote

import jinja2

from robotsix_auto_mail.db import MailRecord
from robotsix_auto_mail.format import _BODY_PREVIEW_LIMIT, _format_date
from robotsix_auto_mail.status import STATUS_ORDER, VALID_STATUSES
from robotsix_auto_mail.triage import (
    RuleLedgerEntry,
    TriageDecision,
    TriageError,
    get_triage_decision,
    list_rule_proposals,
    list_triage_decisions,
    set_rule_state,
)

# Jinja2 environment for the board/detail pages.  ``autoescape`` is enabled
# at the environment level (so the security default is safe), but each
# template wraps its body in ``{% autoescape false %}`` because every
# interpolated value is already escaped in Python via ``html.escape`` (which
# uses ``&quot;``/``&#x27;`` for quotes) — letting Jinja2 autoescape would
# both double-escape and switch to ``markupsafe`` quote sequences, changing
# the emitted bytes.
_JINJA_ENV = jinja2.Environment(autoescape=True)

_BOARD_COLUMNS = STATUS_ORDER

_CSS = """\
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: system-ui, -apple-system, sans-serif;
  background: #e8e8e8; padding: 1.5rem;
}
h1 { margin-bottom: 1rem; font-size: 1.5rem; }
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
  font-size: 1rem; font-weight: 600; text-transform: capitalize;
}
.count {
  background: #666; color: #fff; font-size: 0.75rem;
  font-weight: 600; padding: 0.15rem 0.5rem; border-radius: 999px;
}
.triage-badge {
  display: inline-block; background: #555; color: #fff;
  font-size: 0.7rem; font-weight: 600; text-transform: uppercase;
  padding: 0.1rem 0.4rem; border-radius: 999px; cursor: help;
}
.triage-answer { background: #2563eb; }
.triage-archive { background: #6b7280; }
.triage-delete { background: #b91c1c; }
.triage-ignore { background: #9ca3af; }
.triage-user_triage { background: #d97706; }
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
}"""


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
    '<meta http-equiv="refresh" content="30">\n'
    "<style>{{ css }}</style>\n"
    "</head>\n"
    "<body>\n"
    "<h1>Mail Board</h1>\n"
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
    "  var card = e.target.closest('.card');\n"
    "  if (!card) return;\n"
    "  var mid = card.getAttribute('data-message-id');\n"
    "  if (!mid) return;\n"
    "  e.preventDefault();\n"
    "  var subject = card.getAttribute('data-subject') || '';\n"
    "  openDetail(mid, subject);\n"
    "});\n"
    "</script>\n"
    "</body>\n"
    "</html>"
    "{% endautoescape %}"
)


# Embedded (iframe) detail fragment.  ``fields_html`` is the shared,
# already-escaped inner block.
_DETAIL_EMBED_TEMPLATE = _JINJA_ENV.from_string(
    "{% autoescape false %}"
    '<style>\n'
    '.detail-field { margin-bottom: 0.75rem; }\n'
    '.detail-label { font-weight: 700; font-size: 0.85rem; color: #666;'
    ' margin-bottom: 0.15rem; }\n'
    '.detail-value { font-size: 0.95rem; }\n'
    '.detail-value pre { margin: 0; white-space: pre-wrap;'
    ' font-family: inherit; }\n'
    '.detail-value code { font-size: 0.85rem; background: #eee;'
    ' padding: 0.1rem 0.3rem; border-radius: 3px; }\n'
    '.detail-form { margin-top: 0.25rem; display: flex; gap: 0.25rem;'
    ' align-items: center; }\n'
    '.detail-form select { font-size: 0.8rem; padding: 0.15rem 0.3rem; }\n'
    '.detail-form button { font-size: 0.8rem; padding: 0.15rem 0.6rem;'
    ' cursor: pointer; }\n'
    '.embed-detail { padding: 1rem;'
    ' font-family: system-ui, -apple-system, sans-serif; }\n'
    '</style>\n'
    '<div class="embed-detail">\n'
    "{{ fields_html }}"
    '</div>\n'
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
    '<meta http-equiv="refresh" content="30">\n'
    "<style>{{ css }}</style>\n"
    "</head>\n"
    "<body>\n"
    '<a class="back-link" href="/board">← Back to board</a>\n'
    '<div class="detail-container">\n'
    "<h1>{{ heading }}</h1>\n"
    "{{ fields_html }}"
    '</div>\n'
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


def _build_board_html(db_path: str) -> str:
    """Build the full ``/board`` HTML document.

    Opens a fresh database connection, queries all four status columns,
    and returns a string.  Raises ``Exception`` when the database cannot
    be opened (the caller should catch it and return a 503).
    """
    from robotsix_auto_mail.db import init_db
    from robotsix_auto_mail.status import list_by_status

    conn = init_db(db_path)
    try:
        # Gather records per status in fixed column order.
        columns: list[tuple[str, list[MailRecord]]] = []
        for status in _BOARD_COLUMNS:
            records = list_by_status(conn, status)
            columns.append((status, records))
        # Read every triage decision once and key it by message_id so each
        # card can show its advisory action without a per-card query.
        triage_by_mid: dict[str, TriageDecision] = {
            decision.message_id: decision for decision in list_triage_decisions(conn)
        }
        # List (read-only) the pending deterministic-rule proposals so the
        # board can surface them for human validation.
        proposals = list_rule_proposals(conn, "pending")
    finally:
        conn.close()

    # Build column HTML fragments.
    columns_html_parts = [
        _render_column(status, records, triage_by_mid) for status, records in columns
    ]

    return _BOARD_TEMPLATE.render(
        css=_CSS,
        columns_html="".join(columns_html_parts),
        proposals_html=_render_rule_proposals(proposals),
    )


def _build_detail_html(
    db_path: str, message_id: str, *, embed: bool = False,
) -> str | None:
    """Build a full HTML detail page for a single ``MailRecord``.

    Returns the HTML string, or ``None`` when *message_id* is not found.
    Raises an exception on database errors (caller catches for 503).
    """
    from robotsix_auto_mail.db import get_record_by_message_id, init_db

    conn = init_db(db_path)
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

    # Status options
    options_parts: list[str] = []
    for s in STATUS_ORDER:
        sel = ' selected' if s == record.status else ''
        options_parts.append(
            f'<option value="{html.escape(s)}"{sel}>'
            f'{html.escape(s.capitalize())}</option>'
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
        f'{redirect_input}'
        f'<select name="status">{"".join(options_parts)}</select>'
        '<button type="submit">Move</button>'
        '</form>'
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
        body_html = f'<pre>{html.escape(body)}</pre>'

    # Body HTML note
    body_html_note = ""
    if record.body_html.strip():
        body_html_note = (
            '<div class="detail-field">'
            '<div class="detail-label">HTML version</div>'
            '<div class="detail-value"><em>HTML version available</em></div>'
            '</div>'
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
            '</div>'
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
            '</div>'
        )

    # Triage decision (read-only advisory display).
    if triage_decision is not None:
        triage_value = (
            f'<strong>{html.escape(triage_decision.action)}</strong>'
            f' <span class="triage-source">'
            f'({html.escape(triage_decision.source)},'
            f' {html.escape(triage_decision.confidence)})</span>'
        )
        if triage_decision.reason:
            triage_value += (
                f'<div class="triage-reason">'
                f'{html.escape(triage_decision.reason)}</div>'
            )
    else:
        triage_value = '<em>(no triage decision)</em>'
    triage_section = (
        '<div class="detail-field">'
        '<div class="detail-label">Triage</div>'
        f'<div class="detail-value">{triage_value}</div>'
        '</div>\n'
    )

    # The inner detail fields (Sender through IMAP UID) are identical for
    # the embedded fragment and the full standalone page.
    fields_html = (
        '<div class="detail-field">'
        '<div class="detail-label">Sender</div>'
        f'<div class="detail-value"><strong>{html.escape(record.sender)}'
        '</strong></div>'
        '</div>\n'
        '<div class="detail-field">'
        '<div class="detail-label">Date</div>'
        f'<div class="detail-value">{date_str}</div>'
        '</div>\n'
        '<div class="detail-field">'
        '<div class="detail-label">Status</div>'
        f'<div class="detail-value">{html.escape(record.status.capitalize())}'
        f'{move_form}</div>'
        '</div>\n'
        f'{triage_section}'
        '<div class="detail-field">'
        '<div class="detail-label">To</div>'
        f'<div class="detail-value">{to_html}</div>'
        '</div>\n'
        f'{cc_section}'
        '<div class="detail-field">'
        '<div class="detail-label">Body</div>'
        f'<div class="detail-value">{body_html}</div>'
        '</div>\n'
        f'{body_html_note}'
        '<div class="detail-field">'
        '<div class="detail-label">Attachments</div>'
        f'<div class="detail-value">{attach_html}</div>'
        '</div>\n'
        '<div class="detail-field">'
        '<div class="detail-label">Message ID</div>'
        f'<div class="detail-value"><code>{html.escape(record.message_id)}'
        '</code></div>'
        '</div>\n'
        f'{imap_uid_section}'
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
    status: str,
    records: list[MailRecord],
    triage_by_mid: dict[str, TriageDecision],
) -> str:
    """Render a single board column (header + cards) as an HTML string."""
    title = status.capitalize()
    count = len(records)
    cards_html = "".join(
        _render_card(r, triage_by_mid.get(r.message_id)) for r in records
    )
    return (
        f'<div class="column">'
        f'<div class="column-header"><h2>{html.escape(title)}</h2>'
        f'<span class="count">{count}</span></div>'
        f'<div class="cards">{cards_html}</div>'
        f'</div>'
    )


def _render_card(
    record: MailRecord, decision: TriageDecision | None = None
) -> str:
    """Render a single ``MailRecord`` as a ``.card`` HTML string."""
    sender = html.escape(record.sender)
    subject = html.escape(record.subject) if record.subject.strip() else "(no subject)"
    subject_attr = html.escape(record.subject.strip() or "(no subject)")
    quoted_mid = quote(record.message_id, safe="")
    subject_html = f'<a href="/email/{quoted_mid}">{subject}</a>'
    date_str = html.escape(_format_date(record.date))

    body = record.body_plain
    if not body or not body.strip():
        body_html = '<span class="no-body">(no body)</span>'
    elif len(body) > _BODY_PREVIEW_LIMIT:
        body_html = html.escape(body[:_BODY_PREVIEW_LIMIT]) + "…"
    else:
        body_html = html.escape(body)

    # Build status dropdown with current status pre-selected.
    options_parts: list[str] = []
    for s in _BOARD_COLUMNS:
        sel = ' selected' if s == record.status else ''
        options_parts.append(
            f'<option value="{html.escape(s)}"{sel}>'
            f'{html.escape(s.capitalize())}</option>'
        )

    form_html = (
        '<form class="card-form" method="post" action="/move">'
        f'<input type="hidden" name="message_id"'
        f' value="{html.escape(record.message_id)}">'
        f'<select name="status">{"".join(options_parts)}</select>'
        '<button type="submit">Move</button>'
        '</form>'
    )

    # Read-only triage badge: action label with the reason in a tooltip.
    if decision is not None:
        badge = (
            f'<span class="triage-badge triage-{html.escape(decision.action)}"'
            f' title="{html.escape(decision.reason or "")}">'
            f'{html.escape(decision.action)}</span>'
        )
    else:
        badge = ''

    return (
        f'<div class="card" data-message-id="{quoted_mid}"'
        f' data-subject="{subject_attr}">'
        f'<div class="sender">{sender}</div>'
        f'<div class="subject">{subject_html}</div>'
        f'<div class="date">{date_str}{badge}</div>'
        f'<div class="body-preview">{body_html}</div>'
        f'{form_html}'
        f'</div>'
    )


def _render_rule_card(fingerprint: str, entry: RuleLedgerEntry) -> str:
    """Render one pending rule proposal as a ``.rule-card`` HTML string.

    Every interpolated value is passed through ``html.escape`` because the
    board templates run under ``{% autoescape false %}`` (see ``_JINJA_ENV``).
    """
    title = html.escape(entry.title)
    summary = html.escape(
        f"{entry.match_type}={entry.match_value} -> {entry.action}"
    )
    fp = html.escape(fingerprint)
    return (
        '<div class="rule-card">'
        f'<div class="rule-title">{title}</div>'
        f'<div class="rule-summary">{summary}</div>'
        '<form class="rule-form" method="post" action="/rule-action">'
        f'<input type="hidden" name="fingerprint" value="{fp}">'
        '<button type="submit" name="decision" value="accept">Accept</button>'
        '<button type="submit" name="decision" value="reject">Reject</button>'
        '</form>'
        '</div>'
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
            _render_rule_card(fingerprint, entry)
            for fingerprint, entry in proposals
        )
    else:
        cards_html = (
            '<div class="rule-empty">No pending rule proposals</div>'
        )
    return (
        '<div class="rule-proposals">'
        '<div class="rule-proposals-header">'
        '<h2>Rule proposals</h2>'
        f'<span class="count rule-count">{count}</span></div>'
        f'<div class="rule-cards">{cards_html}</div>'
        '</div>'
    )


class BoardHandler(BaseHTTPRequestHandler):
    """Request handler for the robotsix-auto-mail board server.

    Routes ``GET /`` to a 301 redirect to ``/board``, ``GET /board`` to
    the kanban board HTML page, and everything else to 404.  The target
    SQLite database is injected per-instance via ``db_path``.
    """

    def __init__(self, *args: object, db_path: str, **kwargs: object) -> None:
        # Set the attribute BEFORE calling ``super().__init__`` because
        # ``BaseHTTPRequestHandler.__init__`` invokes ``handle()``
        # synchronously, which dispatches to ``do_GET``/``do_POST``.
        self.db_path = db_path
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def do_GET(self) -> None:
        """Route GET requests via an ordered (predicate → handler) table."""
        routes: list[tuple[Callable[[str], bool], Callable[[], None]]] = [
            (lambda p: p == "/", lambda: self._redirect("/board")),
            (lambda p: p == "/board", self._serve_board),
            (
                lambda p: p.startswith("/email/") and p.endswith("/status"),
                self._serve_email_status,
            ),
            (lambda p: p.startswith("/email/"), self._serve_email_detail),
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
            "/rule-action": self._handle_rule_action,
            "/config-sync": self._handle_config_sync,
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

    def _serve_json(self, payload: dict[str, object], status: int = 200) -> None:
        """Serialize *payload* as JSON and send it with *status*."""
        self._send_response(
            json.dumps(payload),
            status=status,
            content_type="application/json; charset=utf-8",
        )

    def _serve_board(self) -> None:
        """Render and serve the kanban board HTML."""
        try:
            body = _build_board_html(self.db_path)
        except Exception:
            self._send_response("Database unavailable", status=503)
            return

        self._send_response(body, content_type="text/html; charset=utf-8")

    def _handle_move(self) -> None:
        """Process POST /move — update a card's status and redirect."""
        from robotsix_auto_mail.db import init_db
        from robotsix_auto_mail.status import set_status

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        # parse_qs returns {key: [value, ...]} — extract first value.
        message_id = (fields.get("message_id") or [""])[0].strip()
        new_status = (fields.get("status") or [""])[0].strip()
        redirect_to = (fields.get("redirect_to") or [""])[0].strip()

        if not message_id or not new_status:
            self._bad_request("Missing message_id or status")
            return

        if new_status not in VALID_STATUSES:
            self._bad_request(f"Invalid status: {new_status!r}")
            return

        conn = init_db(self.db_path)
        try:
            ok = set_status(conn, message_id, new_status)
        finally:
            conn.close()

        if not ok:
            self._not_found()
            return

        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, code=302)
        else:
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
            from robotsix_auto_mail.config_sync import (
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

    def _serve_email_status(self) -> None:
        """Serve GET /email/{message_id}/status — return status as text."""
        from robotsix_auto_mail.db import init_db
        from robotsix_auto_mail.status import get_status

        # Extract the URL-encoded message_id from the path:
        #   "/email/<encoded>/status" → extract and decode.
        path = self.path
        prefix = "/email/"
        suffix = "/status"
        encoded_mid = path[len(prefix) : -len(suffix)]
        message_id = unquote(encoded_mid)

        conn = init_db(self.db_path)
        try:
            status = get_status(conn, message_id)
        finally:
            conn.close()

        if status is None:
            self._not_found()
            return

        self._send_response(status)

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
        message_id = unquote(parsed.path[len(prefix):])
        qs = parse_qs(parsed.query)
        embed = qs.get("embed", ["0"])[0] == "1"

        try:
            detail_html = _build_detail_html(
                self.db_path, message_id, embed=embed,
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


def make_board_handler(db_path: str) -> functools.partial[BoardHandler]:
    """Return a callable that builds a ``BoardHandler`` wired to *db_path*.

    ``HTTPServer`` calls the result as ``handler(request, client_address,
    server)``; the returned ``functools.partial`` binds *db_path* as a
    keyword argument so the standard three positional args still flow
    through to ``BoardHandler.__init__``.
    """
    return functools.partial(BoardHandler, db_path=db_path)
