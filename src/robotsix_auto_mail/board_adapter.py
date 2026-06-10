"""``MailBoardAdapter`` — satisfies the ``BoardAdapter`` Protocol from
``robotsix_board``.

Maps auto-mail's data model (``MailRecord``, ``TriageDecision``) onto the
eight-method adapter protocol.  The server (``server/__init__.py``) uses
the adapter as the single source of truth for the *base* column/card
scaffold data — column order + labels (``columns``), per-card title
(``card_title``), triage badge (``card_badges``), timestamps
(``card_timestamps``) and the move-form endpoint (``move_endpoint``).

The library's generic ``render_board()`` is intentionally **not**
invoked.  ``render_board()`` passes every adapter return value through
``html.escape(..., quote=True)`` and exposes no per-card raw-HTML
extra-content hook, so auto-mail's custom per-card/per-column widgets —
the archive-proposal selector with override/confirm forms, the delete
button, the draft-reply button, the unsubscribe banner, the
batch-delete and force-triage forms, notes/draft indicators,
``data-message-id``/``data-subject`` attributes and the body preview —
which are structural HTML (forms, buttons, banners) rather than escaped
text, cannot pass through it.  auto-mail therefore server-renders the
fragments itself, layering those widgets on top of the adapter-sourced
base.

Full library-driven rendering via ``render_board()`` is blocked on a
robotsix-board enhancement — a per-card raw-HTML extra-content hook —
which is the path to true adoption and belongs in
``github.com/damien-robotsix/robotsix-board`` rather than this repo.
"""

from __future__ import annotations

from collections.abc import Mapping

from robotsix_board import BoardAdapter, RenderMode

from robotsix_auto_mail.db import MailRecord
from robotsix_auto_mail.format import _format_date
from robotsix_auto_mail.triage import TRIAGE_ACTION_LABELS, TRIAGE_ACTION_ORDER


class MailBoardAdapter:
    """Adapter that presents auto-mail records to the ``robotsix_board`` library.

    The adapter stores enough pre-computed context to answer any protocol
    method without touching the database.  Auto-mail-specific data
    (archive subfolders, folder-exists map, unsubscribe suggestions,
    notes) is also stored for use by the manual board rendering in
    ``server/__init__.py``.

    The server consumes the protocol methods directly to build the base
    column/card scaffold; it does **not** call ``render_board()`` (see the
    module docstring for why — escaping + the absence of a per-card
    raw-HTML hook).
    """

    def __init__(
        self,
        triage_by_mid: Mapping[str, str],
        archive_subfolders: Mapping[str, str],
        folder_exists: Mapping[str, bool],
        archive_root: str,
        unsubscribe_suggestions: Mapping[str, dict[str, object]],
        record_notes: Mapping[str, str],
    ) -> None:
        # Protocol-facing data.
        self._triage_by_mid = dict(triage_by_mid)  # message_id → action

        # Auto-mail-specific data for server.py rendering.
        self.archive_subfolders = dict(archive_subfolders)
        self.folder_exists = dict(folder_exists)
        self.archive_root = archive_root
        self.unsubscribe_suggestions = dict(unsubscribe_suggestions)
        self.record_notes = dict(record_notes)

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
