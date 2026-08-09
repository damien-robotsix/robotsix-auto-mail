"""Adapter and background-task helpers for the board server."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from robotsix_auto_mail.config import (
    DEFAULT_ARCHIVE_ROOT,
    MailConfig,
    resolve_llm_api_key,
)
from robotsix_auto_mail.core._constants import (
    _BATCH_OP_STATE_KEY,
    _RECONCILE_STATE_KEY,
    _TRIAGE_RUN_STATE_KEY,
    _WATERMARK_IDLE,
)
from robotsix_auto_mail.db import (
    MailRecord,
    delete_record_by_message_id,
    set_watermark,
)
from robotsix_auto_mail.server._constants import BATCH_OP_VERBS, _with_db
from robotsix_auto_mail.server.board_adapter import MailBoardAdapter
from robotsix_auto_mail.triage import TO_ARCHIVE, TO_DELETE


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
    and delegates every other attribute — the ``card_*`` scaffold methods
    and the ``card_extra_html`` / ``column_extra_html`` raw-HTML hooks —
    to the wrapped :class:`MailBoardAdapter`.
    """

    def __init__(self, adapter: MailBoardAdapter, status_keys: list[str]) -> None:
        self._adapter = adapter
        self._status_keys = status_keys

    def columns(self) -> list[tuple[str, str]]:
        labels = dict(self._adapter.columns())
        return [(key, labels[key]) for key in self._status_keys]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._adapter, name)


def _run_triage_background(
    db_path: str,
    user_email: str | None = None,
    rules_path: str | None = None,
) -> None:
    """Run the triage agent in a background thread, clearing the watermark on exit.

    Opens its own SQLite connection so it never shares a connection with
    the HTTP request-serve thread.  The ``triage_run:state`` watermark
    is always set back to ``"idle"`` in a ``finally`` block — even when the
    triage module cannot be imported or ``run_triage_agent`` raises.
    """
    from robotsix_auto_mail.db import set_watermark

    with _with_db(db_path, skip_migrations=True) as conn:
        try:
            try:
                from robotsix_auto_mail.triage import (
                    run_triage_agent,
                )
            except ImportError:
                return
            run_triage_agent(
                conn,
                user_email=user_email,
                rules_path=rules_path,
                only_undecided=True,
            )
        except Exception:  # noqa: S110  # nosec B110
            # Swallow all exceptions — the watermark is always cleared.
            pass
        finally:
            set_watermark(conn, _TRIAGE_RUN_STATE_KEY, _WATERMARK_IDLE)


def _run_reconcile_background(db_path: str, mail_config: MailConfig | None) -> None:
    """Run reconcile_records in a background thread, clearing the watermark on exit.

    Opens its own SQLite connection and IMAP connection so it never shares
    a connection with the HTTP request-serve thread.  The ``reconcile:state``
    watermark is always set back to ``"idle"`` in a ``finally`` block.

    After healing/removing stale mail records, also cleans up empty
    auto-created archive subfolders (see
    :func:`~robotsix_auto_mail.db.archive.cleanup_empty_archive_folders`).
    """
    import logging

    logger = logging.getLogger(__name__)

    from robotsix_auto_mail.db import set_watermark
    from robotsix_auto_mail.imap import ImapClient, ImapError
    from robotsix_auto_mail.pipeline import reconcile_records

    with _with_db(db_path, skip_migrations=True) as conn:
        try:
            if mail_config is None:
                return
            try:
                with ImapClient(mail_config) as client:
                    healed, removed = reconcile_records(
                        conn, client, monitored_folder=mail_config.imap_folder
                    )
                    logger.info("reconcile_done healed=%s removed=%s", healed, removed)

                    # Clean up empty archive subfolders.
                    if mail_config.archive_enabled:
                        try:
                            from robotsix_auto_mail.db.archive import (
                                cleanup_empty_archive_folders,
                            )

                            deleted, skipped = cleanup_empty_archive_folders(
                                client,
                                archive_root=mail_config.archive_root,
                            )
                            if deleted:
                                logger.info(
                                    "archive_cleanup_done deleted=%s skipped=%s",
                                    deleted,
                                    skipped,
                                )
                        except Exception:
                            logger.exception("archive_cleanup_failed")
            except ImapError as exc:
                logger.warning("reconcile_imap_error error=%s", str(exc))
        except Exception:  # noqa: S110  # nosec B110
            # Swallow all exceptions — the watermark is always cleared.
            pass
        finally:
            set_watermark(conn, _RECONCILE_STATE_KEY, _WATERMARK_IDLE)


def _batch_op_running(state: str | None) -> bool:
    """Return whether *state* (the ``batch_op:state`` watermark) means running.

    "Running" is any value that is neither ``None`` nor the literal
    ``"idle"`` — i.e. the JSON progress payload set while a batch worker
    is in flight.
    """
    return state is not None and state != _WATERMARK_IDLE


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


def _ensure_folder_hierarchy(client: Any, dest_folder: str, delimiter: str) -> None:
    """Ensure the IMAP folder hierarchy for *dest_folder* exists.

    Creates each ancestor folder from the top down (e.g. ``Archive``,
    ``Archive/2024``, ``Archive/2024/02``) so the final destination is
    guaranteed to exist before a move or copy.
    """
    parts = dest_folder.split(delimiter)
    for i in range(1, len(parts) + 1):
        client.create_folder(delimiter.join(parts[:i]))


def _imap_cross_folder_fallback(
    mail_config: MailConfig,
    record: MailRecord,
    conn: Any,
) -> tuple[str, int] | None:
    """Resolve a stale-UID message across IMAP folders.

    Opens a new :class:`~robotsix_auto_mail.imap.ImapClient`, calls
    :func:`~robotsix_auto_mail.imap.cross_folder_resolve`, updates the
    local DB source, and returns ``(new_folder, new_uid)`` on success.
    Returns ``None`` when the message cannot be found in any folder.
    """
    from robotsix_auto_mail.db import update_record_source
    from robotsix_auto_mail.imap import ImapClient, cross_folder_resolve

    with ImapClient(mail_config) as client:
        cross = cross_folder_resolve(client, record.message_id)
        if cross is not None:
            new_folder, new_uid = cross
            update_record_source(
                conn,
                record.message_id,
                source_folder=new_folder,
                imap_uid=new_uid,
            )
            return new_folder, new_uid
    return None


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


def _run_db_only_batch_op(
    conn: Any, records: list[MailRecord], op_str: str, done: int, total: int
) -> int:
    """Run a DB-only batch operation, deleting each record and updating progress.

    Returns the updated *done* count.
    """
    for record in records:
        delete_record_by_message_id(conn, record.message_id)
        conn.commit()
        done += 1
        set_watermark(
            conn,
            _BATCH_OP_STATE_KEY,
            _batch_progress(op_str, done, total),
        )
    return done


def _run_batch_background(
    db_path: str,
    mail_config: MailConfig | None,
    *,
    action: str,
    action_constant: str,
    group_records_fn: Any,
    process_group_fn: Any,
    pre_filter: Any | None = None,
) -> None:
    """Parameterised driver for batch background operations (delete / archive).

    Owns the SQLite connection, swallows all exceptions, and always resets
    the ``batch_op:state`` watermark to ``"idle"`` in a ``finally`` block.

    Parameters
    ----------
    action:
        Progress verb (e.g. ``"delete"``, ``"archive"``).
    action_constant:
        Triage action constant used to collect records
        (``TO_DELETE`` / ``TO_ARCHIVE``).
    group_records_fn:
        Called as ``group_records_fn(conn, client, mail_config, records)``
        inside the IMAP client context.  Must return a ``dict`` mapping
        group keys to lists of :class:`MailRecord`.
    process_group_fn:
        Called as ``process_group_fn(client, conn, mail_config, group_key,
        group_records)`` for each group.  Must perform all IMAP operations,
        delete DB rows, commit, and return the number of records processed.
    pre_filter:
        Optional ``pre_filter(conn, records)`` called after collecting
        records; returns the (possibly filtered) record list.
    """
    from robotsix_auto_mail.db import set_watermark
    from robotsix_auto_mail.imap import ImapClient

    with _with_db(db_path, skip_migrations=True) as conn:
        try:
            records = _collect_records_for_action(conn, action_constant)
            if pre_filter is not None:
                records = pre_filter(conn, records)
            total = len(records)
            set_watermark(
                conn,
                _BATCH_OP_STATE_KEY,
                _batch_progress(action, 0, total),
            )

            need_imap = mail_config is not None and any(
                r.imap_uid is not None for r in records
            )
            done = 0
            if need_imap and mail_config is not None:
                with ImapClient(mail_config) as client:
                    groups = group_records_fn(conn, client, mail_config, records)
                    for group_key, group_records in groups.items():
                        n = process_group_fn(
                            client, conn, mail_config, group_key, group_records
                        )
                        done += n
                        set_watermark(
                            conn,
                            _BATCH_OP_STATE_KEY,
                            _batch_progress(action, done, total),
                        )
            else:
                _run_db_only_batch_op(conn, records, action, done, total)
        except Exception:  # noqa: S110  # nosec B110
            # Swallow all exceptions — the watermark is always cleared.
            pass
        finally:
            set_watermark(conn, _BATCH_OP_STATE_KEY, _WATERMARK_IDLE)


def _run_batch_delete_background(db_path: str, mail_config: MailConfig | None) -> None:
    """Delete every ``TO_DELETE`` mail from IMAP + local DB in the background.

    Thin wrapper around :func:`_run_batch_background`.  Records are processed
    in chunks of :data:`~robotsix_auto_mail.imap._BATCH_UID_CHUNK`; each
    chunk issues one batched ``client.delete_messages(...)``, deletes the
    chunk's local rows and commits, then bumps the ``done`` count in
    the watermark.  Committing per chunk is what makes a mid-batch restart
    leave the already-processed mails removed from the DB, so re-triggering
    naturally skips them.  Records with ``imap_uid is None`` are DB-only
    deletes.
    """
    from collections import defaultdict

    from robotsix_auto_mail.db import delete_record_by_message_id
    from robotsix_auto_mail.imap import (
        _BATCH_UID_CHUNK,
        ImapMessageNotFoundError,
        resolve_uid_with_fallback,
    )

    def _group_by_folder(
        conn: Any, client: Any, mail_config: Any, records: list[MailRecord]
    ) -> dict[str, list[MailRecord]]:
        by_folder: dict[str, list[MailRecord]] = defaultdict(list)
        for r in records:
            by_folder[r.source_folder].append(r)
        return by_folder

    def _process_delete_group(
        client: Any,
        conn: Any,
        mail_config: Any,
        source_folder: str,
        group_records: list[MailRecord],
    ) -> int:
        # Resolve possibly-stale UIDs in this folder.
        resolved: list[tuple[MailRecord, int]] = []
        for r in group_records:
            if r.imap_uid is None:
                resolved.append((r, 0))
            else:
                try:
                    new_uid = resolve_uid_with_fallback(
                        client,
                        source_folder,
                        r.imap_uid,
                        r.message_id,
                    )
                except ImapMessageNotFoundError:
                    result = _imap_cross_folder_fallback(mail_config, r, conn)
                    if result is not None:
                        new_folder, new_uid = result
                        client.select_folder(new_folder)
                        client.delete_message(new_uid)
                    resolved.append((r, 0))
                else:
                    resolved.append((r, new_uid))

        # Process in chunks.
        n = 0
        for start in range(0, len(resolved), _BATCH_UID_CHUNK):
            chunk = resolved[start : start + _BATCH_UID_CHUNK]
            uids = [uid for _, uid in chunk if uid]
            if uids:
                # Re-select folder before batch delete
                # (cross_folder_resolve may have left us
                # on a different folder).
                client.select_folder(source_folder)
                client.delete_messages(uids)
            for record, _ in chunk:
                delete_record_by_message_id(conn, record.message_id)
            conn.commit()
            n += len(chunk)
        return n

    _run_batch_background(
        db_path,
        mail_config,
        action="delete",
        action_constant=TO_DELETE,
        group_records_fn=_group_by_folder,
        process_group_fn=_process_delete_group,
    )


def _run_batch_archive_background(
    db_path: str,
    mail_config: MailConfig | None,
    archive_root: str = DEFAULT_ARCHIVE_ROOT,
    subfolder_filter: str | None = None,
) -> None:
    """Archive every ``TO_ARCHIVE`` mail from IMAP + local DB in the background.

    Thin wrapper around :func:`_run_batch_background`.  When
    *subfolder_filter* is not ``None``, only records whose effective archive
    subfolder (per :func:`get_archive_subfolder`) equals it are archived —
    the rest of the ``TO_ARCHIVE`` column is left untouched.  This backs the
    per-destination "Archive this folder" buttons; ``None`` archives the
    whole column (the "Archive All" button).

    Each record's destination differs, so UIDs are grouped by their
    effective destination subfolder (the same logic the board uses for
    ``TO_ARCHIVE``) and each group is batch-moved with one
    :meth:`ImapClient.move_messages` call.  The destination folder hierarchy
    is created before the move.  DB rows are deleted and committed per group
    so a mid-batch restart leaves the processed groups removed
    (re-triggering then skips them).  Records with ``imap_uid is None`` are
    DB-only deletes.  All exceptions are swallowed and ``batch_op:state`` is
    always reset to ``"idle"`` in ``finally``.
    """
    from collections import defaultdict

    from robotsix_auto_mail.db import delete_record_by_message_id
    from robotsix_auto_mail.imap import (
        ImapMessageNotFoundError,
        resolve_uid_with_fallback,
    )
    from robotsix_auto_mail.triage import get_archive_subfolder, rules_text_for

    rules = rules_text_for(mail_config)
    _delimiter: str = ""

    def _pre_filter(conn: Any, records: list[MailRecord]) -> list[MailRecord]:
        if subfolder_filter is None:
            return records
        fkey = resolve_llm_api_key(raise_on_missing=False)
        return [
            r
            for r in records
            if get_archive_subfolder(conn, r.message_id, r, api_key=fkey, rules=rules)
            == subfolder_filter
        ]

    def _group_by_source_dest(
        conn: Any, client: Any, mail_config: Any, records: list[MailRecord]
    ) -> dict[tuple[str, str], list[MailRecord]]:
        nonlocal _delimiter
        _delimiter = next(
            (f.delimiter for f in client.list_folders() if f.delimiter),
            "/",
        )
        effective_root = (
            mail_config.archive_root if mail_config is not None else archive_root
        )
        by_source_dest: dict[tuple[str, str], list[MailRecord]] = defaultdict(list)
        api_key = resolve_llm_api_key(raise_on_missing=False)
        for record in records:
            subfolder = get_archive_subfolder(
                conn,
                record.message_id,
                record,
                api_key=api_key,
                rules=rules,
            )
            dest = _archive_dest_folder(effective_root, subfolder, _delimiter)
            if dest is None:
                # Destination escapes the archive root — skip.
                continue
            by_source_dest[(record.source_folder, dest)].append(record)
        return by_source_dest

    def _process_archive_group(
        client: Any,
        conn: Any,
        mail_config: Any,
        group_key: tuple[str, str],
        group_records: list[MailRecord],
    ) -> int:
        from robotsix_auto_mail.db import write_archive_audit_entry
        from robotsix_auto_mail.triage import (
            TO_ARCHIVE,
            get_archive_subfolder_with_source,
        )

        source_folder, dest = group_key
        api_key = resolve_llm_api_key(raise_on_missing=False)
        # Resolve UIDs in source_folder.
        resolved_uids: list[int] = []
        for r in group_records:
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
                result = _imap_cross_folder_fallback(mail_config, r, conn)
                if result is not None:
                    new_folder, new_uid = result
                    _ensure_folder_hierarchy(client, dest, _delimiter)
                    client.select_folder(new_folder)
                    client.move_message(new_uid, dest)
                # else: UID truly gone — skip IMAP,
                # still delete DB row.
            else:
                resolved_uids.append(new_uid)

        if resolved_uids:
            # Re-select source_folder (cross_folder_resolve may
            # have left us on a different folder).
            client.select_folder(source_folder)
            # Ensure the destination hierarchy exists.
            _ensure_folder_hierarchy(client, dest, _delimiter)
            client.move_messages(resolved_uids, dest)

        for record in group_records:
            # Write audit entry before deleting the local row.
            subfolder, proposal_source = get_archive_subfolder_with_source(
                conn,
                record.message_id,
                record,
                api_key=api_key,
                rules=rules,
            )
            with contextlib.suppress(Exception):
                # Non-fatal: archive succeeds even if audit write fails
                write_archive_audit_entry(
                    conn,
                    message_id=record.message_id,
                    subject=record.subject,
                    sender=record.sender,
                    date=record.date,
                    source_column=TO_ARCHIVE,
                    source_folder=record.source_folder,
                    dest_folder=subfolder,
                    proposal_source=proposal_source,
                )
            delete_record_by_message_id(conn, record.message_id)
        conn.commit()
        return len(group_records)

    _run_batch_background(
        db_path,
        mail_config,
        action="archive",
        action_constant=TO_ARCHIVE,
        group_records_fn=_group_by_source_dest,
        process_group_fn=_process_archive_group,
        pre_filter=_pre_filter,
    )
