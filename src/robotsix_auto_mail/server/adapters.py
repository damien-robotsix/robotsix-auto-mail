"""Adapter and background-task helpers for the board server."""

from __future__ import annotations

import json
from typing import Any

from robotsix_auto_mail.config import DEFAULT_ARCHIVE_ROOT, MailConfig
from robotsix_auto_mail.db import MailRecord
from robotsix_auto_mail.server._constants import BATCH_OP_VERBS
from robotsix_auto_mail.server.board_adapter import MailBoardAdapter


def _batch_progress(op: str, done: int, total: int) -> str:
    """Return a ``batch_op:state`` progress JSON payload.

    *op* must be a member of `BATCH_OP_VERBS` — the single source of
    truth for valid batch-operation verbs.
    """
    if op not in BATCH_OP_VERBS:
        raise ValueError(f"Unknown batch op verb: {op!r}")
    return json.dumps({"op": op, "done": done, "total": total})


class _NonEmptyColumnsAdapter:
    """Adapter view exposing only the populated columns to ``render_board``.

    auto-mail hides empty columns, but ``render_board`` renders one column
    per :meth:`MailBoardAdapter.columns` entry.  This thin wrapper scopes
    ``columns()`` to *status_keys* (the non-empty columns, in board order)
    and delegates every other attribute — the ``card_*`` scaffold methods,
    ``move_endpoint`` and the ``card_extra_html`` / ``column_extra_html``
    raw-HTML hooks — to the wrapped :class:`MailBoardAdapter`.
    """

    def __init__(self, adapter: MailBoardAdapter, status_keys: list[str]) -> None:
        self._adapter = adapter
        self._status_keys = status_keys

    def columns(self) -> list[tuple[str, str]]:
        labels = dict(self._adapter.columns())
        return [(key, labels[key]) for key in self._status_keys]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._adapter, name)


def _run_triage_background(db_path: str, user_email: str | None = None) -> None:
    """Run the triage agent in a background thread, clearing the watermark on exit.

    Opens its own SQLite connection so it never shares a connection with
    the HTTP request-serve thread.  The ``triage_run:state`` watermark
    is always set back to ``"idle"`` in a ``finally`` block — even when the
    triage module cannot be imported or ``run_triage_agent`` raises.
    """
    from robotsix_auto_mail.db import init_db, set_watermark

    conn = init_db(db_path, skip_migrations=True)
    try:
        try:
            from robotsix_auto_mail.triage import (
                run_triage_agent,
            )
        except ImportError:
            return
        run_triage_agent(conn, user_email=user_email)
    except Exception:  # noqa: S110  # nosec B110
        # Swallow all exceptions — the watermark is always cleared.
        pass
    finally:
        set_watermark(conn, "triage_run:state", "idle")
        conn.close()


def _run_reconcile_background(db_path: str, mail_config: MailConfig | None) -> None:
    """Run reconcile_records in a background thread, clearing the watermark on exit.

    Opens its own SQLite connection and IMAP connection so it never shares
    a connection with the HTTP request-serve thread.  The ``reconcile:state``
    watermark is always set back to ``"idle"`` in a ``finally`` block.
    """
    import structlog

    logger = structlog.get_logger(__name__)

    from robotsix_auto_mail.db import init_db, set_watermark
    from robotsix_auto_mail.imap import ImapClient, ImapError
    from robotsix_auto_mail.pipeline import reconcile_records

    conn = init_db(db_path, skip_migrations=True)
    try:
        if mail_config is None:
            return
        try:
            with ImapClient(mail_config) as client:
                healed, removed = reconcile_records(
                    conn, client, monitored_folder=mail_config.imap_folder
                )
                logger.info("reconcile_done", healed=healed, removed=removed)
        except ImapError as exc:
            logger.warning("reconcile_imap_error", error=str(exc))
    except Exception:  # noqa: S110  # nosec B110
        # Swallow all exceptions — the watermark is always cleared.
        pass
    finally:
        set_watermark(conn, "reconcile:state", "idle")
        conn.close()


def _batch_op_running(state: str | None) -> bool:
    """Return whether *state* (the ``batch_op:state`` watermark) means running.

    "Running" is any value that is neither ``None`` nor the literal
    ``"idle"`` — i.e. the JSON progress payload set while a batch worker
    is in flight.
    """
    return state is not None and state != "idle"


def _archive_dest_folder(
    effective_root: str, subfolder: str | None, delimiter: str
) -> str | None:
    """Compute the destination IMAP folder for a TO_ARCHIVE record.

    Mirrors the destination computation and security gate in
    :meth:`BoardHandler._imap_archive_move`: translates ``/`` separators
    in *subfolder* to the server *delimiter*, joins under *effective_root*,
    and rejects (returns ``None``) any destination that escapes the
    archive root or contains a ``..`` path segment.
    """
    if subfolder:
        translated = subfolder.replace("/", delimiter)
        dest = f"{effective_root}{delimiter}{translated}"
    else:
        dest = effective_root
    root_prefix = f"{effective_root}{delimiter}"
    if dest != effective_root and not dest.startswith(root_prefix):
        return None
    if ".." in dest.split(delimiter):
        return None
    return dest


def _collect_records_for_action(conn: Any, action: str) -> list[MailRecord]:
    """Return the ``MailRecord``s whose current triage decision is *action*."""
    from robotsix_auto_mail.db import get_record_by_message_id
    from robotsix_auto_mail.triage import list_triage_decisions

    records: list[MailRecord] = []
    for decision in list_triage_decisions(conn):
        if decision.action != action:
            continue
        record = get_record_by_message_id(conn, decision.message_id)
        if record is not None:
            records.append(record)
    return records


def _run_batch_delete_background(db_path: str, mail_config: MailConfig | None) -> None:
    """Delete every ``TO_DELETE`` mail from IMAP + local DB in the background.

    Mirrors :func:`_run_triage_background`: owns its SQLite connection,
    swallows all exceptions, and always resets the ``batch_op:state``
    watermark to ``"idle"`` in a ``finally`` block.  Records are processed
    in chunks of :data:`~robotsix_auto_mail.imap._BATCH_UID_CHUNK`; each
    chunk issues one batched ``client.delete_messages(...)``, deletes the
    chunk's local rows and ``commit``s, then bumps the ``done`` count in
    the watermark.  Committing per chunk is what makes a mid-batch restart
    leave the already-processed mails removed from the DB, so re-triggering
    naturally skips them.  Records with ``imap_uid is None`` are DB-only
    deletes.
    """
    from robotsix_auto_mail.db import (
        delete_record_by_message_id,
        init_db,
        set_watermark,
        update_record_source,
    )
    from robotsix_auto_mail.imap import (
        _BATCH_UID_CHUNK,
        ImapClient,
        ImapMessageNotFoundError,
        cross_folder_resolve,
        resolve_uid_with_fallback,
    )

    conn = init_db(db_path, skip_migrations=True)
    try:
        records = _collect_records_for_action(conn, "TO_DELETE")
        total = len(records)
        set_watermark(
            conn,
            "batch_op:state",
            _batch_progress("delete", 0, total),
        )

        need_imap = mail_config is not None and any(
            r.imap_uid is not None for r in records
        )
        done = 0
        if need_imap and mail_config is not None:
            with ImapClient(mail_config) as client:
                # Group records by source_folder so each batch
                # operates in the correct mailbox.
                from collections import defaultdict

                by_folder: dict[str, list[MailRecord]] = defaultdict(list)
                for r in records:
                    by_folder[r.source_folder].append(r)

                for folder, group in by_folder.items():
                    # Resolve possibly-stale UIDs in this folder.
                    resolved: list[tuple[MailRecord, int]] = []
                    for r in group:
                        if r.imap_uid is None:
                            resolved.append((r, 0))
                        else:
                            try:
                                new_uid = resolve_uid_with_fallback(
                                    client,
                                    folder,
                                    r.imap_uid,
                                    r.message_id,
                                )
                            except ImapMessageNotFoundError:
                                cross = cross_folder_resolve(client, r.message_id)
                                if cross is not None:
                                    new_folder, new_uid = cross
                                    update_record_source(
                                        conn,
                                        r.message_id,
                                        source_folder=new_folder,
                                        imap_uid=new_uid,
                                    )
                                    # Immediately delete individually —
                                    # client is still selected on new_folder
                                    # and the UID won't exist in folder.
                                    client.delete_message(new_uid)
                                    resolved.append((r, 0))
                                else:
                                    resolved.append((r, 0))
                            else:
                                resolved.append((r, new_uid))

                    # Process in chunks.
                    for start in range(0, len(resolved), _BATCH_UID_CHUNK):
                        chunk = resolved[start : start + _BATCH_UID_CHUNK]
                        uids = [uid for _, uid in chunk if uid]
                        if uids:
                            # Re-select folder before batch delete
                            # (cross_folder_resolve may have left us
                            # on a different folder).
                            client.select_folder(folder)
                            client.delete_messages(uids)
                        for record, _ in chunk:
                            delete_record_by_message_id(conn, record.message_id)
                        conn.commit()
                        done += len(chunk)
                        set_watermark(
                            conn,
                            "batch_op:state",
                            _batch_progress("delete", done, total),
                        )
        else:
            # DB-only delete (no IMAP configured or no tracked UIDs).
            for record in records:
                delete_record_by_message_id(conn, record.message_id)
                conn.commit()
                done += 1
                set_watermark(
                    conn,
                    "batch_op:state",
                    _batch_progress("delete", done, total),
                )
    except Exception:  # noqa: S110  # nosec B110
        # Swallow all exceptions — the watermark is always cleared.
        pass
    finally:
        set_watermark(conn, "batch_op:state", "idle")
        conn.close()


def _run_batch_archive_background(
    db_path: str,
    mail_config: MailConfig | None,
    archive_root: str = DEFAULT_ARCHIVE_ROOT,
    subfolder_filter: str | None = None,
) -> None:
    """Archive every ``TO_ARCHIVE`` mail from IMAP + local DB in the background.

    When *subfolder_filter* is not ``None``, only records whose effective
    archive subfolder (per :func:`get_archive_subfolder`) equals it are
    archived — the rest of the ``TO_ARCHIVE`` column is left untouched.  This
    backs the per-destination "Archive this folder" buttons; ``None`` archives
    the whole column (the "Archive All" button).

    Mirrors :func:`_run_batch_delete_background` but each record's
    destination differs, so UIDs are grouped by their effective destination
    subfolder (the same logic the board uses for ``TO_ARCHIVE``) and each
    group is batch-moved with one :meth:`ImapClient.move_messages` call.
    The destination folder hierarchy is created before the move.  DB rows
    are deleted and committed per group so a mid-batch restart leaves the
    processed groups removed (re-triggering then skips them).  Records with
    ``imap_uid is None`` are DB-only deletes.  All exceptions are swallowed
    and ``batch_op:state`` is always reset to ``"idle"`` in ``finally``.
    """
    from robotsix_auto_mail.db import (
        delete_record_by_message_id,
        init_db,
        set_watermark,
        update_record_source,
    )
    from robotsix_auto_mail.imap import (
        ImapClient,
        ImapMessageNotFoundError,
        cross_folder_resolve,
        resolve_uid_with_fallback,
    )
    from robotsix_auto_mail.triage import get_archive_subfolder

    conn = init_db(db_path, skip_migrations=True)
    try:
        records = _collect_records_for_action(conn, "TO_ARCHIVE")
        if subfolder_filter is not None:
            fkey = mail_config.llm_api_key if mail_config else ""
            records = [
                r
                for r in records
                if get_archive_subfolder(conn, r.message_id, r, api_key=fkey)
                == subfolder_filter
            ]
        total = len(records)
        set_watermark(
            conn,
            "batch_op:state",
            _batch_progress("archive", 0, total),
        )

        namespace = mail_config.archive_namespace if mail_config is not None else ""
        effective_root = namespace + archive_root
        done = 0

        need_imap = mail_config is not None and any(
            r.imap_uid is not None for r in records
        )
        if need_imap and mail_config is not None:
            with ImapClient(mail_config) as client:
                delimiter = next(
                    (f.delimiter for f in client.list_folders() if f.delimiter),
                    "/",
                )
                # Group records by (source_folder, destination).
                from collections import defaultdict

                by_source_dest: dict[tuple[str, str], list[MailRecord]] = defaultdict(
                    list
                )
                api_key = mail_config.llm_api_key if mail_config else ""
                for record in records:
                    subfolder = get_archive_subfolder(
                        conn,
                        record.message_id,
                        record,
                        api_key=api_key,
                    )
                    dest = _archive_dest_folder(effective_root, subfolder, delimiter)
                    if dest is None:
                        # Destination escapes the archive root — skip.
                        continue
                    by_source_dest[(record.source_folder, dest)].append(record)

                for (source_folder, dest), group in by_source_dest.items():
                    # Resolve UIDs in source_folder.
                    resolved_uids: list[int] = []
                    for r in group:
                        if r.imap_uid is None:
                            continue
                        try:
                            new_uid = resolve_uid_with_fallback(
                                client,
                                source_folder,
                                r.imap_uid,
                                r.message_id,
                            )
                        except ImapMessageNotFoundError:
                            cross = cross_folder_resolve(client, r.message_id)
                            if cross is not None:
                                new_folder, new_uid = cross
                                update_record_source(
                                    conn,
                                    r.message_id,
                                    source_folder=new_folder,
                                    imap_uid=new_uid,
                                )
                                # Immediately move individually — client is
                                # still selected on new_folder and the UID
                                # won't exist in source_folder.
                                parts = dest.split(delimiter)
                                for i in range(1, len(parts) + 1):
                                    client.create_folder(delimiter.join(parts[:i]))
                                client.move_message(new_uid, dest)
                            # else: UID truly gone — skip IMAP,
                            # still delete DB row.
                        else:
                            resolved_uids.append(new_uid)

                    if resolved_uids:
                        # Re-select source_folder (cross_folder_resolve
                        # may have left us on a different folder).
                        client.select_folder(source_folder)
                        # Ensure the destination hierarchy exists.
                        parts = dest.split(delimiter)
                        for i in range(1, len(parts) + 1):
                            client.create_folder(delimiter.join(parts[:i]))
                        client.move_messages(resolved_uids, dest)

                    for record in group:
                        delete_record_by_message_id(conn, record.message_id)
                    conn.commit()
                    done += len(group)
                    set_watermark(
                        conn,
                        "batch_op:state",
                        _batch_progress("archive", done, total),
                    )
        else:
            # DB-only archive (no IMAP configured or no tracked UIDs).
            for record in records:
                delete_record_by_message_id(conn, record.message_id)
                conn.commit()
                done += 1
                set_watermark(
                    conn,
                    "batch_op:state",
                    _batch_progress("archive", done, total),
                )
    except Exception:  # noqa: S110  # nosec B110
        # Swallow all exceptions — the watermark is always cleared.
        pass
    finally:
        set_watermark(conn, "batch_op:state", "idle")
        conn.close()
