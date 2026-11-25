"""Batch IMAP message-mutation primitives over whole UID sets.

Extracted from :mod:`robotsix_auto_mail.imap.client` so ``ImapClient`` can
stay focused on connection lifecycle and single-message operations.  The
functions here operate only on a raw ``imaplib.IMAP4`` connection plus a
``UID SEARCH`` callback (``ImapClient.search_uids``) that validates which
UIDs still exist in the selected folder — they hold no dependency on
``ImapClient`` internals.  ``ImapClient`` keeps thin delegating methods
(``delete_messages``, ``move_messages``) that wire these up.
"""

from __future__ import annotations

import imaplib
import re
from collections.abc import Callable, Iterator
from typing import Any

from .errors import ImapError
from .utils import _encode_mailbox

# Maximum number of UIDs packed into a single ``UID STORE`` / ``UID COPY``
# round-trip by the batched delete/move primitives.  A 518-mail column then
# costs at most six round-trip pairs instead of one per message.
_BATCH_UID_CHUNK = 100


def _copyuid_indicates_empty_source(data: Any) -> bool:
    """Return ``True`` when a COPY response carries an empty ``COPYUID``.

    Inspects the ``UID COPY`` response data for a ``COPYUID`` response
    code (RFC 4315: ``COPYUID <uidvalidity> <source-set> <dest-set>``).
    Returns ``True`` only when ``COPYUID`` is present AND its source-UID
    set is empty (zero messages copied).  Returns ``False`` when no
    ``COPYUID`` is present, so servers without UIDPLUS are not regressed.
    """
    if not data:
        return False
    for item in data:
        if isinstance(item, bytes):
            text = item.decode("utf-8", errors="replace")
        elif isinstance(item, str):
            text = item
        else:
            continue
        match = re.search(r"COPYUID\s+\d+\s+(\S*)", text)
        if match is None:
            continue
        source_set = match.group(1).strip()
        return source_set == ""
    return False


def _filter_valid_uids(
    search_uids: Callable[[str], list[int]], uids: list[int]
) -> Iterator[tuple[str, list[int]]]:
    """Yield ``(valid_set_str, valid_uids_list)`` for each batch of *uids*.

    Chunks *uids* into groups of :data:`_BATCH_UID_CHUNK` and performs a
    ``UID SEARCH`` pre-verification on each chunk (via *search_uids*) to
    filter out stale UIDs that are no longer present in the selected
    folder.  Chunks where every UID is stale are silently skipped.
    """
    for start in range(0, len(uids), _BATCH_UID_CHUNK):
        chunk = uids[start : start + _BATCH_UID_CHUNK]
        uid_set = ",".join(str(uid) for uid in chunk)
        existing = set(search_uids(f"UID {uid_set}"))
        valid_uids = [uid for uid in chunk if uid in existing]
        if not valid_uids:
            continue
        valid_set = ",".join(str(uid) for uid in valid_uids)
        yield valid_set, valid_uids


def delete_messages(
    conn: imaplib.IMAP4 | None,
    search_uids: Callable[[str], list[int]],
    uids: list[int],
) -> None:
    """Mark a whole UID set ``\\Deleted`` and expunge, in chunks.

    Processes *uids* in chunks of :data:`_BATCH_UID_CHUNK`, issuing a
    single ``UID STORE +FLAGS (\\Deleted)`` over the comma-joined UID
    set followed by **one** ``EXPUNGE`` per chunk.  A 518-mail delete
    therefore costs at most six round-trip pairs instead of one per
    message.  An empty list is a no-op (no IMAP calls).

    Stale UIDs (those no longer present in the selected folder) are
    silently filtered out via a ``UID SEARCH`` pre-verification (through
    *search_uids*) before the destructive ``UID STORE`` is issued.  A
    chunk where every UID is stale is skipped entirely.

    Raises :class:`ImapError` if *conn* is ``None`` (not connected) or
    the server returns a non-OK status for any ``UID STORE`` or
    ``EXPUNGE``.
    """
    if not uids:
        return
    if conn is None:
        raise ImapError("Not connected")

    for valid_set, _valid_uids in _filter_valid_uids(search_uids, uids):
        status, _ = conn.uid("STORE", valid_set, "+FLAGS", "(\\Deleted)")
        if status != "OK":
            raise ImapError(
                f"UID STORE +FLAGS (\\Deleted) for UID set "
                f"{valid_set!r} failed: {status}"
            )

        status, _ = conn.expunge()
        if status != "OK":
            raise ImapError(f"EXPUNGE failed: {status}")


def move_messages(
    conn: imaplib.IMAP4 | None,
    search_uids: Callable[[str], list[int]],
    uids: list[int],
    dest_folder: str,
) -> None:
    """Copy a whole UID set to *dest_folder* then delete the originals.

    Processes *uids* in chunks of :data:`_BATCH_UID_CHUNK`: a single
    ``UID COPY`` over the comma-joined UID set to *dest_folder*, followed
    by a batched ``delete_messages()`` of that same set.  An empty list
    is a no-op (no IMAP calls).

    Stale UIDs are silently filtered out via a ``UID SEARCH``
    pre-verification (through *search_uids*) before ``UID COPY`` is
    issued.  After COPY, the ``COPYUID`` response code (RFC 4315) is
    inspected — if it indicates zero source messages were copied,
    deletion of the originals is skipped.

    Raises :class:`ImapError` if *conn* is ``None`` (not connected) or
    the server returns a non-OK status for any ``UID COPY`` or the
    subsequent deletion.
    """
    if not uids:
        return
    if conn is None:
        raise ImapError("Not connected")

    for valid_set, valid_uids in _filter_valid_uids(search_uids, uids):
        status, data = conn.uid("COPY", valid_set, _encode_mailbox(dest_folder))
        if status != "OK":
            raise ImapError(
                f"UID COPY of {valid_set!r} to {dest_folder!r} failed: {status}"
            )

        # Defensive hardening: if the server advertises UIDPLUS it
        # returns a ``COPYUID`` response code.  When present and its
        # source-UID set is empty the COPY affected zero messages —
        # skip deletion of originals.
        if _copyuid_indicates_empty_source(data):
            continue

        delete_messages(conn, search_uids, valid_uids)
