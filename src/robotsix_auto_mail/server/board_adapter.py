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
from robotsix_auto_mail.format import (
    _BODY_PREVIEW_LIMIT,
    _effective_body_plain,
    _format_date,
)
from robotsix_auto_mail.triage import (
    TRIAGE_ACTION_LABELS,
    TRIAGE_ACTION_ORDER,
    _sender_key,
)


def _account_qs(account_id: str | None) -> str:
    """Return ``"?account=<quoted id>"`` or ``""`` when *account_id* is None."""
    if account_id is None:
        return ""
    return "?account=" + quote(account_id, safe="")


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

        In aggregate mode (``self._record_accounts`` non-empty) each card
        carries a ``data-account`` attribute, a visible account badge, and
        every per-card form ``action`` appends ``?account=<owning id>`` so
        POSTs route to the correct account's DB.
        """
        current_action = self._triage_by_mid.get(card.message_id, "INBOX")
        subject_str = card.subject.strip() or "(no subject)"
        subject_attr = html.escape(subject_str)
        escaped_mid = html.escape(card.message_id)
        quoted_mid = quote(card.message_id, safe="")

        # Aggregate-mode context for this card.
        account_id = self._record_accounts.get(card.message_id)
        account_qs = _account_qs(account_id) if account_id else ""
        # In aggregate ("All mailboxes") mode each form posts to the card's own
        # ``?account=<id>`` so the write hits the right DB — but that also
        # switches the account cookie, so a plain redirect to ``/board`` would
        # land on that single account.  Send actions back to the aggregate
        # board instead.  (Empty for single-account views, which redirect to
        # ``/board`` and rely on the existing cookie.)
        aggregate_redirect = (
            '<input type="hidden" name="redirect_to" value="/board?account=__all__">'
            if account_id
            else ""
        )

        # Account badge (aggregate mode only).
        account_badge = ""
        data_account_attr = ""
        if account_id:
            data_account_attr = f' data-account="{html.escape(account_id)}"'
            label = self._account_labels.get(account_id, account_id)
            account_badge = f'<span class="card-account">{html.escape(label)}</span>'

        # Body preview.
        body = _effective_body_plain(card)
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

        # Draft-reply button (TO_ANSWER only) — a single click POSTs to
        # /generate-draft so the LLM prepares a draft reply.  No
        # ``redirect_to`` is sent, so the handler falls back to the trusted
        # ``/board#{message_id}`` redirect that re-opens the panel showing
        # the generated draft.
        draft_button = ""
        if current_action == "TO_ANSWER":
            draft_button = (
                '<form class="draft-reply-form" method="post"'
                f' action="/generate-draft{account_qs}">'
                f'<input type="hidden" name="message_id" value="{escaped_mid}">'
                '<button type="submit" class="draft-reply-btn">'
                "Draft reply</button>"
                "</form>"
            )

        # Delete form (TO_DELETE only).
        delete_form = ""
        if current_action == "TO_DELETE":
            delete_form = (
                '<form class="delete-form" method="post"'
                f' action="/delete{account_qs}"'
                ' onsubmit="return confirm('
                "'Permanently delete this mail from mailbox and database?')\">"
                f'<input type="hidden" name="message_id" value="{escaped_mid}">'
                f"{aggregate_redirect}"
                '<button type="submit" class="delete-btn">Delete</button>'
                "</form>"
            )

        # Archive proposal section (TO_ARCHIVE only).
        archive_html = ""
        # ``data-archive-dest`` lets the board JS group TO_ARCHIVE cards by
        # destination and offer a per-folder "Archive these" button.  Empty
        # value means the archive root.
        data_archive_dest_attr = ""
        if current_action == "TO_ARCHIVE":
            arc_subfolder = self.archive_subfolders.get(card.message_id)
            if arc_subfolder is not None:
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

        move_url, move_method = self.move_endpoint(card)
        move_form = (
            f'<form class="board-card-move" method="{html.escape(move_method)}"'
            f' action="{html.escape(move_url)}{account_qs}">'
            f'<input type="hidden" name="message_id" value="{escaped_mid}">'
            f"{aggregate_redirect}"
            '<select class="board-move-select" name="triage_action">'
            f"{''.join(options_parts)}</select>"
            '<button type="submit" class="board-move-submit">Move</button>'
            "</form>"
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
        if status_key == "TO_DELETE" and not self._batch_running:
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
        if status_key == "TO_ARCHIVE" and not self._batch_running and not aggregate:
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
        if status_key == "TO_ARCHIVE":
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
        if status_key != "INBOX" and not aggregate:
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
