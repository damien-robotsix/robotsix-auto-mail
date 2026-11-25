"""Low-level IMAP transport primitives for the board's archive endpoints.

Hosts the transport-level helpers extracted from
:class:`~robotsix_auto_mail.server._archive_service.ArchiveService`:
``_find_message_in_archive`` (searching every folder under an archive root
for a given message id) and ``_imap_archive_move`` (moving a message from
its origin folder into the archive destination).  Neither helper touches
request context — all per-call state arrives as parameters.  Path checks
reuse the shared helpers in :mod:`robotsix_auto_mail.server._archive_paths`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from robotsix_auto_mail.server._archive_paths import imap_delimiter

if TYPE_CHECKING:
    from robotsix_auto_mail.config import MailConfig


def _find_message_in_archive(
    client: Any,
    message_id: str,
    archive_root: str,
    delimiter: str,
) -> tuple[str, int] | None:
    """Search all folders under *archive_root* for *message_id*.

    Returns ``(folder_name, uid)`` or ``None`` when not found.
    Only searches folders whose name starts with *archive_root*.
    Skips folders with ``\\Noselect`` attribute.
    """
    root_prefix = f"{archive_root}{delimiter}"
    for folder in client.list_folders():
        if any(attr.lower() == "\\noselect" for attr in folder.attributes):
            continue
        if folder.name != archive_root and not folder.name.startswith(root_prefix):
            continue
        client.select_folder(folder.name)
        uids = client.search_uids(f'HEADER Message-ID "{message_id}"')
        if uids:
            return (folder.name, uids[0])
    return None


def _imap_archive_move(
    mail_config: MailConfig,
    imap_uid: int,
    effective_root: str,
    subfolder: str | None,
    source_folder: str = "INBOX",
    message_id: str = "",
) -> None:
    """Move a message to the archive folder via IMAP.

    Selects *source_folder* (the record's origin folder) rather
    than assuming ``config.imap_folder``.  If the stored UID is
    stale, falls back to a ``HEADER Message-ID`` search before
    giving up.

    Raises ValueError on security-policy violations (caller should
    return 400).  Raises ImapError or OSError on IMAP/IO failures
    (caller should return 502).
    """
    from robotsix_auto_mail.imap import ImapClient, resolve_uid_with_fallback
    from robotsix_auto_mail.server.adapters import _archive_dest_folder

    with ImapClient(mail_config) as client:
        # Resolve the possibly-stale UID, selecting source_folder.
        resolved_uid = resolve_uid_with_fallback(
            client, source_folder, imap_uid, message_id
        )

        # Determine the IMAP hierarchy delimiter.
        existing = client.list_folders()
        delimiter = imap_delimiter(existing)

        dest_folder = _archive_dest_folder(effective_root, subfolder, delimiter)
        if dest_folder is None:
            raise ValueError("Archive destination escapes archive root")

        # -- ensure destination folder hierarchy exists ----
        from robotsix_auto_mail.server.adapters import _ensure_folder_hierarchy

        _ensure_folder_hierarchy(client, dest_folder, delimiter)

        client.move_message(resolved_uid, dest_folder)
