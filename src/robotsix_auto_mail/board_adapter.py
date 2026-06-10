"""``MailBoardAdapter`` — satisfies the ``BoardAdapter`` Protocol from
``robotsix_board``.

Maps auto-mail's data model (``MailRecord``, ``TriageDecision``) onto the
adapter protocol.  The server (``server/__init__.py``) drives the full
board grid through the library's generic ``render_board()``: the adapter
is the single source of truth for the *base* column/card scaffold —
column order + labels (``columns``), per-card title (``card_title``),
triage badge (``card_badges``), timestamps (``card_timestamps``) and the
move-form endpoint (``move_endpoint``).

``render_board()`` passes every base adapter return value through
``html.escape(..., quote=True)``.  auto-mail's custom per-card/per-column
widgets — the archive-proposal selector with override/confirm forms, the
delete button, the draft-reply button, the unsubscribe banner, the
batch-delete and force-triage forms, notes/draft indicators,
``data-message-id``/``data-subject`` attributes and the body preview —
are structural HTML (forms, buttons, banners) rather than escaped text,
so they ride on the library's per-card / per-column raw-HTML
extra-content hooks (``card_extra_html`` / ``column_extra_html``).  These
hooks are appended **verbatim** (unescaped) by ``render_board()``, so the
adapter owns its own escaping for any interpolated user data.

The hooks are duck-typed — ``render_board()`` looks them up via
``getattr`` and they are deliberately omitted from the runtime-checkable
``BoardAdapter`` Protocol, so the import-time ``isinstance`` compliance
check is unaffected by their presence.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from urllib.parse import quote

from robotsix_board import BoardAdapter, RenderMode

from robotsix_auto_mail.db import MailRecord
from robotsix_auto_mail.format import _BODY_PREVIEW_LIMIT, _format_date
from robotsix_auto_mail.triage import (
    TRIAGE_ACTION_LABELS,
    TRIAGE_ACTION_ORDER,
    _sender_key,
)


class MailBoardAdapter:
    """Adapter that presents auto-mail records to the ``robotsix_board`` library.

    The adapter stores enough pre-computed context to answer any protocol
    method without touching the database.  Auto-mail-specific data
    (archive subfolders, folder-exists map, unsubscribe suggestions,
    notes) is also stored for use by the manual board rendering in
    ``server/__init__.py``.

    The server (``server/__init__.py``) renders the board grid by calling
    the library's generic ``render_board(adapter, cards)``: this adapter
    is the single source of truth for both the *base* scaffold (the
    protocol methods) and auto-mail's custom per-card/per-column widgets,
    which ride on the duck-typed ``card_extra_html`` / ``column_extra_html``
    raw-HTML hooks.
    """

    def __init__(
        self,
        triage_by_mid: Mapping[str, str],
        archive_subfolders: Mapping[str, str],
        folder_exists: Mapping[str, bool],
        archive_root: str,
        unsubscribe_suggestions: Mapping[str, dict[str, object]],
        record_notes: Mapping[str, str],
        column_records: Mapping[str, Sequence[MailRecord]] | None = None,
    ) -> None:
        # Protocol-facing data.
        self._triage_by_mid = dict(triage_by_mid)  # message_id → action

        # Auto-mail-specific data for server.py rendering.
        self.archive_subfolders = dict(archive_subfolders)
        self.folder_exists = dict(folder_exists)
        self.archive_root = archive_root
        self.unsubscribe_suggestions = dict(unsubscribe_suggestions)
        self.record_notes = dict(record_notes)
        # Per-column bucketed records, used by ``column_extra_html`` (which
        # only receives a ``status_key``, not the column's cards).
        self._column_records: dict[str, list[MailRecord]] = {
            key: list(records) for key, records in (column_records or {}).items()
        }

    # -- BoardAdapter protocol ------------------------------------------------

    def columns(self) -> list[tuple[str, str]]:
        """Return ordered ``(action, label)`` pairs for every triage column."""
        return [
            (action, TRIAGE_ACTION_LABELS[action]) for action in TRIAGE_ACTION_ORDER
        ]

    def card_id(self, card: MailRecord) -> str:
        """Return the stable message-id for *card*."""
        return card.message_id

    def card_title(self, card: MailRecord) -> str:
        """Return ``"sender: subject"`` (or ``"sender: (no subject)"``)."""
        sender = card.sender
        subject = card.subject.strip() or "(no subject)"
        return f"{sender}: {subject}"

    def card_badges(self, card: MailRecord) -> list[str]:
        """Return the current triage action label as a single badge, or empty."""
        action = self._triage_by_mid.get(card.message_id, "INBOX")
        label = TRIAGE_ACTION_LABELS.get(action, action)
        return [label]

    def card_timestamps(self, card: MailRecord) -> dict[str, str]:
        """Return ``{"date": _format_date(card.date)}``."""
        return {"date": _format_date(card.date)}

    def move_endpoint(self, card: MailRecord) -> tuple[str, str]:
        """Return ``("/move", "post")`` — all cards share the same endpoint."""
        return ("/move", "post")

    def move_endpoint_template(self) -> str:
        """Return the URL template for the board config in JSON_HYDRATION mode."""
        return "/move/{card_id}/{target_status}"

    def render_mode(self) -> RenderMode:
        """Return ``RenderMode.SERVER_FRAGMENTS``.

        This is the closest of the two ``RenderMode`` values, since
        auto-mail server-renders HTML fragments.  It is advisory only:
        the library's ``render_board()`` is not invoked at all (see the
        module docstring), so the value is never consumed by the library.
        """
        return RenderMode.SERVER_FRAGMENTS

    # -- raw-HTML extra-content hooks (duck-typed, not in the Protocol) --------

    def card_extra_html(self, card: MailRecord) -> str:
        """Return auto-mail's per-card structural widgets.

        ``render_board()`` appends this fragment **verbatim** (unescaped)
        inside ``.board-card`` after the (library) move form, so this
        method owns its own escaping for every interpolated value.  It
        carries the ``data-message-id`` / ``data-subject`` hooks (consumed
        by the board's detail-panel JS), the body preview, notes/draft
        indicators, the archive-proposal selector + override/confirm
        forms, the draft-reply button, the delete form and auto-mail's own
        move form (the library's generic move form is suppressed via CSS,
        since its targets are coupled to the visible columns and auto-mail
        hides empty ones).
        """
        current_action = self._triage_by_mid.get(card.message_id, "INBOX")
        subject_str = card.subject.strip() or "(no subject)"
        subject_attr = html.escape(subject_str)
        escaped_mid = html.escape(card.message_id)
        quoted_mid = quote(card.message_id, safe="")

        # Body preview.
        body = card.body_plain
        if not body or not body.strip():
            body_html_render = '<span class="no-body">(no body)</span>'
        elif len(body) > _BODY_PREVIEW_LIMIT:
            body_html_render = html.escape(body[:_BODY_PREVIEW_LIMIT]) + "…"
        else:
            body_html_render = html.escape(body)

        # Move-form options (all triage actions; the current one selected).
        options_parts: list[str] = []
        for opt_action in TRIAGE_ACTION_ORDER:
            sel = " selected" if opt_action == current_action else ""
            options_parts.append(
                f'<option value="{html.escape(opt_action)}"{sel}>'
                f"{html.escape(TRIAGE_ACTION_LABELS[opt_action])}</option>"
            )

        # Notes indicator.
        notes_indicator = ""
        if card.notes:
            escaped_notes = html.escape(card.notes)
            truncated = escaped_notes[:40] + ("…" if len(escaped_notes) > 40 else "")
            notes_indicator = (
                '<span class="card-notes-indicator"'
                f' title="{escaped_notes}">'
                f"\U0001f4dd {truncated}</span>"
            )

        # Draft indicator.
        draft_indicator = ""
        if current_action == "DRAFT_READY" and card.draft_text:
            escaped_draft = html.escape(card.draft_text)
            truncated = escaped_draft[:40] + ("…" if len(escaped_draft) > 40 else "")
            draft_indicator = (
                '<span class="card-draft-indicator"'
                f' title="{escaped_draft}">'
                f"✉️ {truncated}</span>"
            )

        # Draft-reply button (TO_ANSWER only).
        draft_button = ""
        if current_action == "TO_ANSWER":
            draft_button = (
                '<button type="button"'
                ' class="draft-reply-btn"'
                f" onclick=\"openDetail('{escaped_mid}', '{subject_attr}', true)\""
                ">Draft reply</button>"
            )

        # Delete form (TO_DELETE only).
        delete_form = ""
        if current_action == "TO_DELETE":
            delete_form = (
                '<form class="delete-form" method="post" action="/delete"'
                ' onsubmit="return confirm('
                "'Permanently delete this mail from mailbox and database?')\">"
                f'<input type="hidden" name="message_id" value="{escaped_mid}">'
                '<button type="submit" class="delete-btn">Delete</button>'
                "</form>"
            )

        # Archive proposal section (TO_ARCHIVE only).
        archive_html = ""
        if current_action == "TO_ARCHIVE":
            arc_subfolder = self.archive_subfolders.get(card.message_id)
            if arc_subfolder is not None:
                escaped_subfolder = html.escape(arc_subfolder)
                escaped_root = html.escape(self.archive_root)
                if arc_subfolder:
                    display_path = f"{escaped_root}/{escaped_subfolder}"
                else:
                    display_path = escaped_root + "/"
                exists_indicator = ""
                if self.folder_exists.get(card.message_id):
                    exists_indicator = (
                        '<span class="archive-exists"'
                        ' title="Folder already exists">'
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

        move_url, move_method = self.move_endpoint(card)
        move_form = (
            f'<form class="board-card-move" method="{html.escape(move_method)}"'
            f' action="{html.escape(move_url)}">'
            f'<input type="hidden" name="message_id" value="{escaped_mid}">'
            '<select class="board-move-select" name="triage_action">'
            f"{''.join(options_parts)}</select>"
            '<button type="submit" class="board-move-submit">Move</button>'
            "</form>"
        )

        return (
            '<div class="card-extra"'
            f' data-message-id="{quoted_mid}"'
            f' data-subject="{subject_attr}">'
            f'<div class="body-preview">{body_html_render}</div>'
            f"{notes_indicator}"
            f"{draft_indicator}"
            f"{archive_html}"
            f"{move_form}"
            f"{draft_button}"
            f"{delete_form}"
            "</div>"
        )

    def column_extra_html(self, status_key: str) -> str:
        """Return auto-mail's per-column structural widgets.

        ``render_board()`` appends this fragment **verbatim** (unescaped)
        inside ``.board-column`` after the cards list, so this method owns
        its own escaping.  It carries the batch-delete button, the
        force-triage form and the unsubscribe banner (all ``TO_DELETE`` /
        non-``INBOX`` specific).
        """
        records = self._column_records.get(status_key, [])
        if not records:
            return ""
        label = TRIAGE_ACTION_LABELS.get(status_key, status_key)
        count = len(records)

        # Batch-delete button (TO_DELETE only).
        batch_delete_form = ""
        if status_key == "TO_DELETE":
            batch_delete_form = (
                '<form class="delete-form" method="post" action="/batch-delete"'
                ' onsubmit="return confirm('
                "'Permanently delete ALL mail in this column"
                " from mailbox and database?')\">"
                '<button type="submit" class="delete-btn">Delete All</button>'
                "</form>"
            )

        # Force-triage button (every column except INBOX).
        force_triage_form = ""
        if status_key != "INBOX":
            force_triage_form = (
                '<form class="force-triage-form" method="post"'
                ' action="/force-triage-column"'
                ' onsubmit="return confirm('
                f"'Re-triage all {count} items in {html.escape(label)}?')\">"
                f'<input type="hidden" name="action" value="{html.escape(status_key)}">'
                '<button type="submit" class="force-triage-btn">Force Triage</button>'
                "</form>"
            )

        # Unsubscribe banner (TO_DELETE only).
        unsubscribe_banner_html = ""
        if status_key == "TO_DELETE" and self.unsubscribe_suggestions:
            banner_parts: list[str] = []
            seen_senders: set[str] = set()
            for record in records:
                key = _sender_key(record.sender)
                if key in seen_senders:
                    continue
                seen_senders.add(key)
                suggestion = self.unsubscribe_suggestions.get(key)
                if suggestion is None:
                    continue
                method = str(suggestion.get("method", ""))
                url = str(suggestion.get("url", ""))
                description = str(suggestion.get("description", ""))
                if method == "mailto" or (
                    method == "header" and url.lower().startswith("mailto:")
                ):
                    link_html = f'<a href="{html.escape(url)}">Unsubscribe</a>'
                    note = ""
                elif url.startswith("https://") or url.startswith("http://"):
                    note = (
                        ' <span class="unsubscribe-note">'
                        f"({
                            html.escape(
                                'found in email body'
                                if method == 'body_link'
                                else 'from header'
                            )
                        })"
                        "</span>"
                    )
                    link_html = (
                        f'<a href="{html.escape(url)}" target="_blank" rel="noopener">'
                        "Unsubscribe</a>"
                    )
                else:
                    link_html = ""
                    note = ""
                banner_parts.append(
                    '<div class="unsubscribe-banner">'
                    '<span class="unsubscribe-icon">\U0001f4ec</span>'
                    " You could unsubscribe from "
                    f"<strong>{html.escape(record.sender)}</strong>"
                    " instead of deleting: "
                    f"{html.escape(description)} "
                    f"{link_html}{note}"
                    "</div>"
                )
            unsubscribe_banner_html = "".join(banner_parts)

        return f"{batch_delete_form}{force_triage_form}{unsubscribe_banner_html}"


# Verify the adapter satisfies the Protocol at import time.
def _verify_protocol() -> None:
    """Assert ``MailBoardAdapter`` satisfies ``BoardAdapter``."""
    # Use a minimal, valid instance.
    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="",
        unsubscribe_suggestions={},
        record_notes={},
    )
    assert isinstance(adapter, BoardAdapter), (  # noqa: S101 # nosec B101 - intentional Protocol-compliance check
        "MailBoardAdapter does not satisfy BoardAdapter Protocol"
    )


_verify_protocol()
