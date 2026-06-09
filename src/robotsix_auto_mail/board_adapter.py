"""``MailBoardAdapter`` — satisfies the ``BoardAdapter`` Protocol from
``robotsix_board``.

Maps auto-mail's data model (``MailRecord``, ``TriageDecision``) onto the
eight-method adapter protocol so the shared board library can inspect a
card, build a column header, and populate a move form.  Per-card custom
content (delete button, archive proposal, notes/draft indicators,
draft-reply button, ``data-message-id``/``data-subject`` attributes) is
handled by the manual board rendering in ``server.py`` rather than the
library's generic ``render_board()``.
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
    ``server.py``.
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
        """Return ``RenderMode.SERVER_FRAGMENTS``."""
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
    assert isinstance(adapter, BoardAdapter), (  # noqa: S101
        "MailBoardAdapter does not satisfy BoardAdapter Protocol"
    )


_verify_protocol()
