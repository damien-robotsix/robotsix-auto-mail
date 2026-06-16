"""Email detail rendering functions for the board server."""

from __future__ import annotations

import html
import json
from typing import Any
from urllib.parse import quote

from robotsix_auto_mail.db import MailRecord
from robotsix_auto_mail.format import _effective_body_plain, _format_date
from robotsix_auto_mail.server.views.forms import _render_move_form
from robotsix_auto_mail.triage import (
    TRIAGE_ACTION_LABELS,
    TriageDecision,
    get_triage_decision,
)


def _build_detail_html(
    db_path: str,
    message_id: str,
    *,
    embed: bool = False,
    focus_draft: bool = False,
    current_account_id: str | None = None,
) -> str | None:
    """Build a full HTML detail page for a single ``MailRecord``.

    Returns the HTML string, or ``None`` when *message_id* is not found.
    Raises an exception on database errors (caller catches for 503).

    When *current_account_id* is a real account id (not the aggregate
    sentinel ``"__all__"``), the move form ``action`` and embed-mode
    ``redirect_to`` carry an ``account`` query parameter so the POST
    routes to the correct account's database.
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
    except json.JSONDecodeError, TypeError:
        recipients = {"to": [], "cc": []}
    to_list: list[str] = (
        recipients.get("to", []) if isinstance(recipients, dict) else []
    )
    cc_list: list[str] = (
        recipients.get("cc", []) if isinstance(recipients, dict) else []
    )

    try:
        attachments = json.loads(record.attachments_json)
    except json.JSONDecodeError, TypeError:
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
        account_param = ""
        if current_account_id is not None and current_account_id != "__all__":
            account_param = "&account=" + html.escape(
                quote(current_account_id, safe="")
            )
        redirect_input = (
            '<input type="hidden" name="redirect_to"'
            f' value="/email/{html.escape(quoted_mid)}?embed=1{account_param}">'
        )
    # Pass the account id so the move form action carries ?account=<id>.
    move_account_id: str | None = None
    if current_account_id is not None and current_account_id != "__all__":
        move_account_id = current_account_id
    move_form = _render_move_form(
        record, current_action, redirect_input, account_id=move_account_id
    )

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
    calendar_feedback = _render_calendar_feedback(record)

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
        f"{calendar_feedback}"
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


def _render_calendar_feedback(record: MailRecord) -> str:
    """Render calendar feedback in the mail detail view.

    Returns an inline success or error indicator when
    ``calendar_event_ref`` is set.  Returns an empty string when no
    calendar response has been received yet.
    """
    event_ref = record.calendar_event_ref
    if not event_ref:
        return ""

    if event_ref.startswith("error: "):
        error_msg = event_ref[len("error: ") :] or "Unknown error"
        feedback = (
            ' <span class="calendar-feedback calendar-error"'
            f' title="{html.escape(error_msg, quote=True)}">'
            "\u26a0\ufe0f {}</span>".format(html.escape(error_msg))
        )
    else:
        feedback = (
            ' <span class="calendar-feedback calendar-success"'
            f' title="{html.escape(event_ref, quote=True)}">'
            "\u2705 Event added to calendar: {}</span>".format(
                html.escape(event_ref)
            )
        )

    return (
        '<div class="detail-field">'
        '<div class="detail-label">Calendar</div>'
        '<div class="detail-value">'
        f"{feedback}"
        "</div>"
        "</div>\n"
    )
