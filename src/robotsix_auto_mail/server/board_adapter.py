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
from robotsix_auto_mail.core.format import (
    _BODY_PREVIEW_LIMIT,
    _effective_body_plain,
    _format_date,
)
from robotsix_auto_mail.triage import (
    DRAFT_READY,
    INBOX,
    TO_ANSWER,
    TO_ARCHIVE,
    TO_DELETE,
    TRIAGE_ACTION_LABELS,
    TRIAGE_ACTION_ORDER,
    _sender_key,
)


def _account_qs(account_id: str | None) -> str:
    """Return ``"?account=<quoted id>"`` or ``""`` when *account_id* is None."""
    if account_id is None:
        return ""
    return "?account=" + quote(account_id, safe="")


# -- aggregate-mode helpers ---------------------------------------------------


def _account_qs_for(card: MailRecord, record_accounts: Mapping[str, str]) -> str:
    """Return ``"?account=<id>"`` or ``""`` for *card*."""
    account_id = record_accounts.get(card.message_id)
    return _account_qs(account_id) if account_id else ""


def _aggregate_redirect(card: MailRecord, record_accounts: Mapping[str, str]) -> str:
    """Return a hidden ``redirect_to`` input, or ``""``."""
    if record_accounts.get(card.message_id):
        return '<input type="hidden" name="redirect_to" value="/board?account=__all__">'
    return ""


# -- card_extra_html widget helpers -------------------------------------------


def _body_preview_html(card: MailRecord) -> str:
    """Return the escaped body-preview snippet."""
    body = _effective_body_plain(card)
    if not body or not body.strip():
        return '<span class="no-body">(no body)</span>'
    if len(body) > _BODY_PREVIEW_LIMIT:
        return html.escape(body[:_BODY_PREVIEW_LIMIT]) + "…"
    return html.escape(body)


def _move_options_parts(current_action: str) -> list[str]:
    """Build the ``<option>`` strings for the move-form dropdown."""
    parts: list[str] = []
    for action in TRIAGE_ACTION_ORDER:
        sel = " selected" if action == current_action else ""
        parts.append(
            f'<option value="{html.escape(action)}"{sel}>'
            f"{html.escape(TRIAGE_ACTION_LABELS[action])}</option>"
        )
    return parts


def _notes_indicator(card: MailRecord) -> str:
    """Return the notes-indicator HTML snippet, or ``""``."""
    if not card.notes:
        return ""
    escaped_notes = html.escape(card.notes)
    truncated = escaped_notes[:40] + ("…" if len(escaped_notes) > 40 else "")
    return (
        '<span class="card-notes-indicator"'
        f' title="{escaped_notes}">'
        f"\U0001f4dd {truncated}</span>"
    )


def _draft_indicator(card: MailRecord, current_action: str) -> str:
    """Return the draft-indicator HTML snippet, or ``""``."""
    if current_action != DRAFT_READY or not card.draft_text:
        return ""
    escaped_draft = html.escape(card.draft_text)
    truncated = escaped_draft[:40] + ("…" if len(escaped_draft) > 40 else "")
    return (
        '<span class="card-draft-indicator"'
        f' title="{escaped_draft}">'
        f"✉️ {truncated}</span>"
    )


def _calendar_indicator(card: MailRecord) -> str:
    """Return the calendar-indicator HTML snippet, or ``""``."""
    if not card.calendar_event_ref:
        return ""
    event_ref = card.calendar_event_ref
    if event_ref.startswith("error: "):
        error_msg = event_ref[len("error: ") :]
        return (
            '<span class="card-calendar-indicator card-calendar-error"'
            f' title="{html.escape(error_msg, quote=True)}">'
            "\u26a0\ufe0f</span>"
        )
    return (
        '<span class="card-calendar-indicator card-calendar-success"'
        f' title="{html.escape(event_ref, quote=True)}">'
        "\u2705</span>"
    )


def _draft_reply_button(current_action: str, account_qs: str, escaped_mid: str) -> str:
    """Return the draft-reply button HTML snippet, or ``""``."""
    if current_action != TO_ANSWER:
        return ""
    return (
        '<form class="draft-reply-form" method="post"'
        f' action="/generate-draft{account_qs}">'
        f'<input type="hidden" name="message_id" value="{escaped_mid}">'
        '<button type="submit" class="draft-reply-btn">'
        "Draft reply</button>"
        "</form>"
    )


def _delete_form(
    current_action: str,
    account_qs: str,
    escaped_mid: str,
    aggregate_redirect: str,
) -> str:
    """Return the delete-form HTML snippet, or ``""``."""
    if current_action != TO_DELETE:
        return ""
    return (
        '<form class="delete-form" method="post"'
        f' action="/delete{account_qs}"'
        ' onsubmit="return confirm('
        "'Permanently delete this mail from mailbox and database?')\">"
        f'<input type="hidden" name="message_id" value="{escaped_mid}">'
        f"{aggregate_redirect}"
        '<button type="submit" class="delete-btn">Delete</button>'
        "</form>"
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
        batch_running: bool = False,
        *,
        archive_folders: Sequence[str] = (),
        record_accounts: Mapping[str, str] | None = None,
        account_labels: Mapping[str, str] | None = None,
    ) -> None:
        # Protocol-facing data.
        self._triage_by_mid = dict(triage_by_mid)  # message_id → action
        # When a column-wide batch op (delete/archive) is in flight the
        # Delete-All / Archive-All buttons are suppressed, mirroring how the
        # triage banner replaces the inline controls.
        self._batch_running = batch_running

        # Auto-mail-specific data for server.py rendering.
        self.archive_subfolders = dict(archive_subfolders)
        self.folder_exists = dict(folder_exists)
        self.archive_root = archive_root
        # Existing archive subfolders (relative paths) offered as a datalist
        # dropdown on the per-card archive-override field — still free-text so
        # the user can type a new folder.
        self.archive_folders = list(archive_folders)
        self.unsubscribe_suggestions = dict(unsubscribe_suggestions)
        self.record_notes = dict(record_notes)
        # Per-column bucketed records, used by ``column_extra_html`` (which
        # only receives a ``status_key``, not the column's cards).
        self._column_records: dict[str, list[MailRecord]] = {
            key: list(records) for key, records in (column_records or {}).items()
        }
        # Aggregate-view context: message_id → owning account_id and
        # account_id → display label.  Both empty when not in aggregate mode,
        # preserving byte-for-byte single-account output.
        self._record_accounts: dict[str, str] = dict(record_accounts or {})
        self._account_labels: dict[str, str] = dict(account_labels or {})

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
        action = self._triage_by_mid.get(card.message_id, INBOX)
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

    # -- card_extra_html helpers ----------------------------------------------

    def _current_action(self, card: MailRecord) -> str:
        """Return the triage action for *card* (defaults to INBOX)."""
        return self._triage_by_mid.get(card.message_id, INBOX)

    def _account_badge(self, card: MailRecord) -> tuple[str, str]:
        """Return ``(badge_html, data_account_attr)`` for aggregate mode.

        Returns ``("", "")`` when *card* has no owning account.
        """
        account_id = self._record_accounts.get(card.message_id)
        if not account_id:
            return "", ""
        data_account_attr = f' data-account="{html.escape(account_id)}"'
        label = self._account_labels.get(account_id, account_id)
        account_badge = f'<span class="card-account">{html.escape(label)}</span>'
        return account_badge, data_account_attr

    def _archive_html(
        self,
        current_action: str,
        card: MailRecord,
        account_qs: str,
        aggregate_redirect: str,
        quoted_mid: str,
        escaped_mid: str,
    ) -> tuple[str, str]:
        """Return ``(archive_html, data_archive_dest_attr)`` for TO_ARCHIVE.

        Returns ``("", "")`` when *card* is not in TO_ARCHIVE or has no
        subfolder proposal.
        """
        if current_action != TO_ARCHIVE:
            return "", ""
        arc_subfolder = self.archive_subfolders.get(card.message_id)
        if arc_subfolder is None:
            return "", ""
        data_archive_dest_attr = (
            f' data-archive-dest="{html.escape(arc_subfolder, quote=True)}"'
        )
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
            f' action="/archive-proposal{account_qs}">'
            f'<input type="hidden" name="message_id" value="{escaped_mid}">'
            f"{aggregate_redirect}"
            '<input type="text" name="subfolder"'
            f' value="{escaped_subfolder}"'
            ' list="archive-folders"'
            ' placeholder="subfolder path" size="30">'
        )
        # Browse button -- opens the folder-tree popover to select
        # an existing archive subfolder.  Suppressed when no
        # archive subfolders exist yet (fresh setup) and in
        # aggregate mode (the tree is per-account only).
        if self.archive_folders and not self._record_accounts:
            archive_html += (
                '<button type="button" class="archive-browse-btn"'
                f' data-message-id="{quoted_mid}">'
                "\U0001f4c1 Browse</button>"
            )
        archive_html += (
            '<button type="submit">Set</button>'
            "</form>"
            '<form class="archive-confirm-form" method="post"'
            f' action="/archive{account_qs}"'
            ' onsubmit="return confirm('
            f"'Archive this mail to {display_path}?')\">"
            f'<input type="hidden" name="message_id" value="{escaped_mid}">'
            f"{aggregate_redirect}"
            '<button type="submit" class="archive-btn">Archive</button>'
            "</form>"
            "</div>"
        )
        return archive_html, data_archive_dest_attr

    def _move_form(
        self,
        card: MailRecord,
        account_qs: str,
        aggregate_redirect: str,
        escaped_mid: str,
        options_parts: list[str],
    ) -> str:
        """Return the per-card move-form HTML snippet."""
        move_url, move_method = self.move_endpoint(card)
        return (
            f'<form class="board-card-move"'
            f' method="{html.escape(move_method)}"'
            f' action="{html.escape(move_url)}{account_qs}">'
            f'<input type="hidden" name="message_id" value="{escaped_mid}">'
            f"{aggregate_redirect}"
            '<select class="board-move-select" name="triage_action">'
            f"{''.join(options_parts)}</select>"
            '<button type="submit" class="board-move-submit">Move</button>'
            "</form>"
        )

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

        In aggregate mode (``self._record_accounts`` non-empty) each card
        carries a ``data-account`` attribute, a visible account badge, and
        every per-card form ``action`` appends ``?account=<owning id>`` so
        POSTs route to the correct account's DB.
        """
        current_action = self._current_action(card)
        subject_str = card.subject.strip() or "(no subject)"
        subject_attr = html.escape(subject_str)
        escaped_mid = html.escape(card.message_id)
        quoted_mid = quote(card.message_id, safe="")

        account_qs = _account_qs_for(card, self._record_accounts)
        aggregate_redirect = _aggregate_redirect(card, self._record_accounts)

        account_badge, data_account_attr = self._account_badge(card)
        body_html_render = _body_preview_html(card)
        options_parts = _move_options_parts(current_action)
        notes_indicator = _notes_indicator(card)
        draft_indicator = _draft_indicator(card, current_action)
        calendar_indicator = _calendar_indicator(card)
        draft_button = _draft_reply_button(
            current_action,
            account_qs,
            escaped_mid,
        )
        delete_form = _delete_form(
            current_action,
            account_qs,
            escaped_mid,
            aggregate_redirect,
        )
        archive_html, data_archive_dest_attr = self._archive_html(
            current_action,
            card,
            account_qs,
            aggregate_redirect,
            quoted_mid,
            escaped_mid,
        )
        move_form = self._move_form(
            card,
            account_qs,
            aggregate_redirect,
            escaped_mid,
            options_parts,
        )

        return (
            '<div class="card-extra"'
            f' data-message-id="{quoted_mid}"'
            f' data-subject="{subject_attr}"'
            f"{data_archive_dest_attr}"
            f"{data_account_attr}>"
            f"{account_badge}"
            f'<div class="body-preview">{body_html_render}</div>'
            f"{notes_indicator}"
            f"{draft_indicator}"
            f"{calendar_indicator}"
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

        In aggregate mode (``self._record_accounts`` non-empty) the
        batch-delete, batch-archive, and force-triage forms are suppressed
        — each operates on a whole column spanning multiple accounts and
        cannot route to a single-account POST handler.
        """
        aggregate = bool(self._record_accounts)
        records = self._column_records.get(status_key, [])
        if not records:
            return ""
        label = TRIAGE_ACTION_LABELS.get(status_key, status_key)
        count = len(records)

        # Batch-delete button (TO_DELETE only).  Suppressed while a batch op
        # is running, mirroring how the triage banner replaces its form.
        # In aggregate mode the form targets ``/batch-delete?account=__all__``,
        # which the handler fans out to every account's own DB + IMAP worker.
        batch_delete_form = ""
        if status_key == TO_DELETE and not self._batch_running:
            if aggregate:
                delete_action = "/batch-delete?account=__all__"
                delete_scope = "across ALL mailboxes "
            else:
                delete_action = "/batch-delete"
                delete_scope = ""
            batch_delete_form = (
                f'<form class="delete-form" method="post" action="{delete_action}"'
                ' onsubmit="return confirm('
                f"'Permanently delete ALL mail in this column {delete_scope}"
                " from mailbox and database?')\">"
                '<button type="submit" class="delete-btn">Delete All</button>'
                "</form>"
            )

        # Batch-archive button (TO_ARCHIVE only).  Suppressed while a batch
        # op is running, mirroring the Delete-All button.
        # Also suppressed in aggregate mode.
        batch_archive_form = ""
        if status_key == TO_ARCHIVE and not self._batch_running and not aggregate:
            batch_archive_form = (
                '<form class="archive-form" method="post" action="/batch-archive"'
                ' onsubmit="return confirm('
                "'Archive ALL mail in this column to their proposed"
                " folders?')\">"
                '<button type="submit" class="archive-btn">Archive All</button>'
                "</form>"
            )

        # Datalist of existing archive folders for the per-card override
        # dropdown (TO_ARCHIVE only; emitted once per column).  Union of the
        # managed structure and the destinations currently proposed for this
        # column, so any in-play folder is selectable while the field stays
        # free-text (the user can still type a brand-new folder).
        archive_datalist = ""
        if status_key == TO_ARCHIVE:
            options = {f for f in self.archive_folders if f}
            options.update(v for v in self.archive_subfolders.values() if v)
            opts = "".join(
                f'<option value="{html.escape(folder)}"></option>'
                for folder in sorted(options)
            )
            archive_datalist = f'<datalist id="archive-folders">{opts}</datalist>'

        # Force-triage button (every column except INBOX).
        # Suppressed in aggregate mode.
        force_triage_form = ""
        if status_key != INBOX and not aggregate:
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
        if status_key == TO_DELETE and self.unsubscribe_suggestions:
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
                if url.lower().startswith("mailto:") and method in ("mailto", "header"):
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

        return (
            '<div class="column-extra-top">'
            f"{batch_delete_form}{batch_archive_form}{force_triage_form}</div>"
            f"{archive_datalist}"
            f"{unsubscribe_banner_html}"
        )


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
