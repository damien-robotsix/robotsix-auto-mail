"""Mailbox model and parsing / resolution helpers.

Contains the ``MailboxInfo`` dataclass, special-use detection, LIST-line
parsing, and the cross-folder / fallback UID resolution helpers.  These
reference ``ImapClient`` only as a forward-ref type annotation to keep the
runtime import graph acyclic (``utils`` ← ``mailbox`` ← ``client``).
"""

from __future__ import annotations

import dataclasses
import shlex
from typing import TYPE_CHECKING

from .errors import ImapMessageNotFoundError
from .utils import imap_utf7_decode

if TYPE_CHECKING:
    from .client import ImapClient


# ---------------------------------------------------------------------------
# MailboxInfo
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class MailboxInfo:
    """Describes a single mailbox (folder) on the IMAP server.

    Attributes:
        name: Decoded mailbox name (e.g. ``"INBOX"``,
            ``"[Gmail]/Sent Mail"``).
        attributes: Tuple of flag strings from the LIST response
            (e.g. ``('\\\\HasNoChildren',)`` for a leaf folder,
            ``('\\\\HasChildren', '\\\\Noselect')`` for a namespace node).
        delimiter: Hierarchy delimiter character (e.g. ``"/"``, or ``""``
            when the server uses a flat namespace).
    """

    name: str
    attributes: tuple[str, ...]
    delimiter: str


#: SPECIAL-USE mailbox attributes (RFC 6154), plus Gmail's non-standard
#: ``\Important`` and the ``\Noselect`` container flag.  A mailbox carrying
#: any of these is a system folder — Gmail surfaces ``[Gmail]/All Mail``,
#: ``[Gmail]/Sent Mail``, ``[Gmail]/Trash`` … (and the ``\Noselect``
#: ``[Gmail]`` parent node) this way — not an archive-topic folder.  Stored
#: lower-cased for case-insensitive matching.
_SPECIAL_USE_ATTRIBUTES = frozenset(
    {
        "\\all",
        "\\archive",
        "\\drafts",
        "\\flagged",
        "\\junk",
        "\\sent",
        "\\trash",
        "\\important",
        "\\noselect",
    }
)


def is_special_use(info: MailboxInfo) -> bool:
    """Return ``True`` when *info* is a system / special-use mailbox.

    Recognises RFC 6154 SPECIAL-USE attributes (``\\All``, ``\\Sent``,
    ``\\Drafts``, ``\\Trash``, ``\\Junk``, ``\\Flagged``, ``\\Archive``),
    Gmail's non-standard ``\\Important``, and ``\\Noselect`` container nodes
    such as Gmail's ``[Gmail]`` parent.  Matching is case-insensitive.
    """
    return any(attr.lower() in _SPECIAL_USE_ATTRIBUTES for attr in info.attributes)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_list_line(line: bytes) -> MailboxInfo:
    """Parse a single IMAP ``LIST`` response line into a ``MailboxInfo``.

    Expected format (RFC 3501)::

        (FLAGS) DELIMITER NAME

    Examples::

        (\\HasNoChildren) "/" "INBOX"
        (\\HasChildren \\Noselect) "/" "[Gmail]"
        () "/" "Sent Mail"
        (\\HasNoChildren) NIL "INBOX"
    """
    text = line.decode("utf-8", errors="replace")

    # Flags are always in the first parenthesised group.
    if not text.startswith("("):
        raise ValueError(f"Invalid LIST response: {text!r}")
    end = text.find(")")
    if end < 0:
        raise ValueError(f"Invalid LIST response: {text!r}")

    flags_str = text[1:end]
    rest = text[end + 1 :].strip()

    # The remaining tokens (delimiter + name) may be quoted or NIL.
    try:
        tokens = shlex.split(rest)
    except ValueError as exc:
        raise ValueError(f"Invalid LIST response: {text!r}") from exc

    if len(tokens) < 2:
        raise ValueError(f"Invalid LIST response: {text!r}")

    # -- flags -----------------------------------------------------------
    if flags_str.strip():
        attributes: tuple[str, ...] = tuple(f.strip() for f in flags_str.split())
    else:
        attributes = ()

    # -- delimiter -------------------------------------------------------
    delimiter = "" if tokens[0].upper() == "NIL" else tokens[0]

    # -- name ------------------------------------------------------------
    name = imap_utf7_decode(tokens[1])

    return MailboxInfo(name=name, attributes=attributes, delimiter=delimiter)


def resolve_uid_with_fallback(
    client: "ImapClient",
    source_folder: str,
    uid: int,
    message_id: str,
) -> int:
    """Return a confirmed UID for *message_id* in *source_folder*.

    First tries the stored *uid*.  If that UID is not found in
    *source_folder*, falls back to a ``HEADER Message-ID`` search
    (UIDs can shift when servers renumber).  Raises
    ``ImapMessageNotFoundError`` if neither approach finds the
    message.

    The caller must have already selected a folder or be prepared
    for this function to select *source_folder*.
    """
    client.select_folder(source_folder)
    if client.search_uids(f"UID {uid}"):
        return uid
    # Fallback: UID may have shifted — search by Message-ID header.
    found = client.search_uids(f'HEADER Message-ID "{message_id}"')
    if found:
        return found[0]
    raise ImapMessageNotFoundError(
        f"UID {uid} not found in {source_folder!r} (Message-ID fallback also failed)"
    )


_WASTE_FOLDER_PATTERNS: frozenset[str] = frozenset(
    {
        "trash",
        "deleted items",
        "deleted messages",
        "bin",
        "papierkorb",
        "gelöschte objekte",
        "éléments supprimés",
        "elementi eliminati",
        "junk",
        "spam",
        "bulk mail",
        "junk e-mail",
        "courrier indésirable",
    }
)


def _is_waste_folder(name: str) -> bool:
    """Return ``True`` when *name* is a Trash or Junk folder.

    Case-insensitive substring match against a frozen set of known
    patterns covering English, German, French, and Italian.
    """
    lowered = name.lower()
    return any(pattern in lowered for pattern in _WASTE_FOLDER_PATTERNS)


def cross_folder_resolve(
    client: "ImapClient",
    message_id: str,
) -> tuple[str, int] | None:
    """Search all non-waste folders for *message_id* via its header.

    Calls ``list_folders()``, skips folders where
    ``_is_waste_folder(name)`` is ``True``, selects each remaining folder
    and runs ``UID SEARCH HEADER Message-ID``.  Returns ``(folder_name,
    uid)`` for the first match, or ``None`` when no non-waste folder
    contains the message.

    Non-selectable container nodes (those carrying ``\\Noselect``, e.g.
    Gmail's ``[Gmail]`` namespace parent) are skipped — ``SELECT``-ing them
    fails with ``NO`` and is not a real resolution target.

    Propagates ``ImapError`` (and subclasses) on connection/auth/protocol
    failures — it never swallows transient errors.
    """
    for folder in client.list_folders():
        if _is_waste_folder(folder.name):
            continue
        if any(attr.lower() == "\\noselect" for attr in folder.attributes):
            continue
        client.select_folder(folder.name)
        uids = client.search_uids(f'HEADER Message-ID "{message_id}"')
        if uids:
            return (folder.name, uids[0])
    return None
