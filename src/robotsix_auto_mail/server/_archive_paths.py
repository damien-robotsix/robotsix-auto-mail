"""Shared archive-path helpers for the board server's archive endpoints.

The archive action, view and attachment services all translate a
``/``-separated archive-relative path into the IMAP server's namespace and
must reject any path that escapes the configured archive root.  That check is
security-relevant, so it lives here once instead of being copied into every
handler.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from robotsix_auto_mail.server._board_handler_protocol import RequestContext


def imap_delimiter(folders: Iterable[Any]) -> str:
    """Return the IMAP hierarchy delimiter advertised by *folders*.

    Picks the first non-empty ``delimiter`` attribute; falls back to ``/``.
    """
    return next((f.delimiter for f in folders if f.delimiter), "/")


def translate_archive_path(
    archive_root: str,
    sub: str,
    delimiter: str,
    *,
    allow_root: bool = True,
) -> str | None:
    """Translate archive-relative *sub* into the IMAP namespace.

    Joins *sub* under *archive_root* (an empty *sub* designates the root
    itself), replaces every ``/`` with the server *delimiter* and returns the
    result.  Returns ``None`` when the translated path is not under the
    archive root — a sibling such as ``Archive2`` for root ``Archive`` is
    rejected because the prefix check includes the trailing delimiter.  When
    *allow_root* is ``False`` the root itself is rejected too.  A *sub*
    containing a ``..`` segment is always rejected.
    """
    if ".." in sub.split("/"):
        return None
    full_path = f"{archive_root}/{sub}" if sub else archive_root
    translated = full_path.replace("/", delimiter)
    ar_translated = archive_root.replace("/", delimiter)
    root_prefix = f"{ar_translated}{delimiter}"
    if translated == ar_translated:
        return translated if allow_root else None
    if not translated.startswith(root_prefix):
        return None
    return translated


def send_imap_failure(ctx: RequestContext, exc: Exception, operation: str) -> None:
    """Answer 502 for an IMAP (``ImapError``) or I/O (``OSError``) failure.

    ``ImapError`` yields ``"IMAP error during <operation>: <exc>"``; any other
    exception (an ``OSError``) yields ``"IMAP connection error: <exc>"``.
    """
    from robotsix_auto_mail.imap import ImapError

    if isinstance(exc, ImapError):
        message = f"IMAP error during {operation}: {exc}"
    else:
        message = f"IMAP connection error: {exc}"
    ctx._send_response(message, status=502)
