"""Verify tracked IMAP records: heal moved mails, remove deleted ones."""

from __future__ import annotations

import logging
import sqlite3

from robotsix_auto_mail.imap import ImapClient

_logger = logging.getLogger(__name__)


def reconcile_records(
    db_conn: sqlite3.Connection,
    imap_client: ImapClient,
    monitored_folder: str | None = None,
) -> tuple[int, int]:
    """Verify tracked records' UIDs; heal moved mails, remove deleted ones.

    The board only ever tracks mail that lives in the account's *monitored*
    folder (``imap_folder``): ingest stamps ``source_folder`` to it, and every
    board action that moves a message elsewhere (archive/move/delete) also
    removes the record.  So a live record's ``source_folder`` should always
    equal *monitored_folder*.  When *monitored_folder* is given, any record
    whose ``source_folder`` differs from it has drifted out of the monitored
    folder — the user archived/moved it directly in their mailbox (or it was
    left behind by the pre-#502 reconcile that healed cross-folder moves
    instead of pruning them) — and is pruned outright.

    When *monitored_folder* is ``None`` the drift check is skipped (legacy
    behaviour): each ``source_folder`` group is reconciled in place.

    Returns ``(healed, removed)`` counts.
    """
    from robotsix_auto_mail.db import delete_record_by_message_id, update_record_source
    from robotsix_auto_mail.imap import ImapError, cross_folder_resolve

    try:
        # 1. Query all records with a tracked IMAP UID.
        cur = db_conn.execute(
            "SELECT message_id, source_folder, imap_uid "
            "FROM mail_records WHERE imap_uid IS NOT NULL"
        )
        rows = cur.fetchall()
        if not rows:
            return (0, 0)

        # 2. Build {folder: {uid: message_id}} mapping.
        folder_uids: dict[str, dict[int, str]] = {}
        for message_id, source_folder, imap_uid in rows:
            folder_uids.setdefault(source_folder, {})[imap_uid] = message_id

        healed = 0
        removed = 0

        # 3. For each source folder, verify tracked UIDs are still present.
        for folder, uid_map in folder_uids.items():
            # Any record whose source_folder is not the monitored folder has
            # left the board's only valid location — prune the whole group
            # without an IMAP round-trip.  This self-heals records stranded by
            # the pre-#502 reconcile, which rewrote source_folder to wherever a
            # user-moved mail landed instead of pruning it.
            if monitored_folder is not None and folder != monitored_folder:
                for message_id in uid_map.values():
                    delete_record_by_message_id(db_conn, message_id)
                    removed += 1
                db_conn.commit()
                continue

            # Select the folder — skip on error (e.g. folder was deleted).
            try:
                imap_client.select_folder(folder)
            except ImapError:
                _logger.warning("reconcile_select_error folder=%s", folder)
                continue

            tracked_uids = set(uid_map.keys())
            found_uids: set[int] = set()

            # Chunk UIDs in groups of 500 for the UID SEARCH.
            uid_list = sorted(tracked_uids)
            folder_failed = False
            for i in range(0, len(uid_list), 500):
                chunk = uid_list[i : i + 500]
                criteria = "UID " + ",".join(str(u) for u in chunk)
                try:
                    found_uids.update(imap_client.search_uids(criteria))
                except ImapError:
                    _logger.warning("reconcile_search_error folder=%s", folder)
                    folder_failed = True
                    break

            if folder_failed:
                continue  # Skip this folder entirely on transient error.

            missing_uids = tracked_uids - found_uids

            # 4. For each missing UID, cross-folder resolve to heal or remove.
            for missing_uid in missing_uids:
                message_id = uid_map[missing_uid]
                try:
                    resolved = cross_folder_resolve(
                        imap_client, message_id, source_folder=folder
                    )
                except ImapError:
                    _logger.warning("reconcile_resolve_error message_id=%s", message_id)
                    continue

                if resolved is not None:
                    new_folder, new_uid = resolved
                    if new_folder == folder:
                        # Same folder, new UID (e.g. UIDVALIDITY change) → heal.
                        update_record_source(
                            db_conn,
                            message_id,
                            source_folder=new_folder,
                            imap_uid=new_uid,
                        )
                        db_conn.commit()
                        healed += 1
                    else:
                        # Different folder → user (not auto-mail) moved the
                        # message; prune the record.
                        delete_record_by_message_id(db_conn, message_id)
                        db_conn.commit()
                        removed += 1
                else:
                    delete_record_by_message_id(db_conn, message_id)
                    db_conn.commit()
                    removed += 1

        return (healed, removed)

    except ImapError as exc:
        _logger.warning("reconcile_imap_error error=%s", str(exc))
        return (0, 0)
