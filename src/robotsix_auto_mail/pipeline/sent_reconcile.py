"""Auto-archive board cards whose composed reply has been sent.

Compose-drafts do not create local card records; instead each reply-draft
records a :class:`~robotsix_auto_mail.db.ComposeLink` binding the original
card's ``Message-ID`` to a pending send.  This module scans the account's
Sent folder for the sent reply (matched by its ``In-Reply-To`` header, which
both the board-composed draft and an externally-sent copy carry) and, when
found, archives the still-open card by upserting a ``TO_ARCHIVE`` triage
decision — so a sent draft never lingers as a phantom card.

Reconciliation is idempotent (each link is stamped reconciled once handled)
and never false-archives: a card is only archived when a matching message is
actually present in the Sent folder.
"""

from __future__ import annotations

import logging
import sqlite3

from robotsix_auto_mail.imap import ImapClient

_logger = logging.getLogger(__name__)


def _discover_sent_folder(client: ImapClient) -> str | None:
    """Return the IMAP Sent-folder name, or ``None`` when absent.

    Prefers the RFC 6154 ``\\Sent`` SPECIAL-USE attribute; falls back to
    the first folder whose name contains ``"sent"`` (case-insensitive) for
    servers that do not advertise SPECIAL-USE.
    """
    for info in client.list_folders():
        if any(attr.lower() == "\\sent" for attr in info.attributes):
            return info.name
    for info in client.list_folders():
        if "sent" in info.name.lower():
            return info.name
    return None


def _imap_quote(value: str) -> str:
    """Sanitise *value* for embedding in an IMAP SEARCH quoted string.

    Strips the IMAP quoted-string terminators (``"`` and ``\\``) and any
    CR/LF so a hostile ``Message-ID`` cannot break out of the search
    argument or inject additional protocol commands.
    """
    return "".join(ch for ch in value if ch not in '"\\\r\n')


def reconcile_sent_drafts(
    db_conn: sqlite3.Connection,
    imap_client: ImapClient,
) -> tuple[int, int]:
    """Archive cards whose composed reply now appears in the Sent folder.

    For every unreconciled compose link, searches the Sent folder for a
    message whose ``In-Reply-To`` header references the original card's
    ``Message-ID``.  When found, upserts a ``TO_ARCHIVE`` triage decision
    for the still-present card (an operator's own ``user`` decision is never
    overwritten) and stamps the link reconciled.  A card that has already
    been removed just has its link stamped.

    Returns ``(archived, checked)``: the number of cards archived and the
    number of pending links inspected.
    """
    from robotsix_auto_mail.db import (
        get_record_by_message_id,
        list_unreconciled_compose_links,
        mark_compose_link_reconciled,
    )
    from robotsix_auto_mail.imap import ImapError
    from robotsix_auto_mail.triage import TO_ARCHIVE, set_triage_decision

    links = list_unreconciled_compose_links(db_conn)
    if not links:
        return (0, 0)

    try:
        sent_folder = _discover_sent_folder(imap_client)
        if sent_folder is None:
            _logger.info("sent_reconcile_no_sent_folder pending=%s", len(links))
            return (0, len(links))
        imap_client.select_folder(sent_folder)
    except ImapError as exc:
        _logger.warning("sent_reconcile_select_error error=%s", str(exc))
        return (0, len(links))

    archived = 0
    checked = 0
    for link in links:
        checked += 1
        mid = link.reply_to_message_id
        criteria = f'HEADER In-Reply-To "{_imap_quote(mid)}"'
        try:
            uids = imap_client.search_uids(criteria)
        except ImapError:
            _logger.warning("sent_reconcile_search_error message_id=%s", mid)
            continue
        if not uids:
            # Reply not sent yet — never false-archive.
            continue

        record = get_record_by_message_id(db_conn, mid)
        if record is not None:
            set_triage_decision(
                db_conn,
                record.message_id,
                TO_ARCHIVE,
                source="agent",
                reason="auto-archived: composed reply detected in Sent folder",
            )
            archived += 1
            _logger.info(
                "sent_reconcile_archived message_id=%s sent_uids=%s",
                record.message_id,
                len(uids),
            )
        else:
            _logger.info("sent_reconcile_card_gone message_id=%s", mid)
        mark_compose_link_reconciled(db_conn, mid)

    return (archived, checked)
