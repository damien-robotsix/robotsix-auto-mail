"""Adapter and background-task helpers for the board server."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from robotsix_auto_mail.board_adapter import MailBoardAdapter
from robotsix_auto_mail.config import DEFAULT_ARCHIVE_ROOT, MailConfig
from robotsix_auto_mail.db import MailRecord


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
    the HTTP request-serve thread.  After triaging, derives fresh
    deterministic rule proposals from the updated triage history (no LLM)
    and records the genuinely-new ones as ``pending`` so the board can
    surface them for human validation.  The ``triage_run:state`` watermark
    is always set back to ``"idle"`` in a ``finally`` block — even when the
    triage module cannot be imported or ``run_triage_agent`` raises.
    """
    from robotsix_auto_mail.db import init_db, set_watermark

    conn = init_db(db_path, skip_migrations=True)
    try:
        try:
            from robotsix_auto_mail.triage import (
                propose_triage_rules,
                record_and_filter_rule_proposals,
                run_triage_agent,
            )
        except ImportError:
            return
        run_triage_agent(conn, user_email=user_email)
        # Surface freshly-derived rule proposals on the board.  This is a
        # deterministic, LLM-free scan of triage history, so it is cheap to
        # run on every triage pass; record_and_filter only writes the
        # ledger when there is a genuinely-new proposal.
        record_and_filter_rule_proposals(conn, propose_triage_rules(conn))
    except Exception:  # noqa: S110  # nosec B110
        # Swallow all exceptions — the watermark is always cleared.
        pass
    finally:
        set_watermark(conn, "triage_run:state", "idle")
        conn.close()


def _run_folder_triage_background(
    db_path: str, mail_config: MailConfig, folder: str
) -> None:
    """Ingest a named IMAP folder then run the triage agent over it.

    Modelled on :func:`_run_triage_background`: opens its own SQLite
    connection (never shared with the request-serve thread), swallows all
    exceptions so a missing ``pydantic_ai``, an ``ImapError`` or a bad
    folder never wedges the board, and always sets the ``triage_run:state``
    watermark back to ``"idle"`` in a ``finally`` block.  ``ingest_folder``
    selects the explicit *folder*, searches ``ALL`` and dedups by
    Message-ID; it does not touch the INBOX watermark or create archive
    folders.
    """
    from robotsix_auto_mail.db import init_db, set_watermark

    conn = init_db(db_path, skip_migrations=True)
    try:
        try:
            from robotsix_auto_mail.triage import (
                propose_triage_rules,
                record_and_filter_rule_proposals,
                run_triage_agent,
            )
        except ImportError:
            return
        from robotsix_auto_mail.imap import ImapClient
        from robotsix_auto_mail.pipeline import ingest_folder

        with ImapClient(mail_config) as imap:
            ingest_folder(conn, imap, mail_config, folder)
        run_triage_agent(conn, user_email=mail_config.username)
        # Mirror the inbox helper's deterministic rule-proposal refresh.
        record_and_filter_rule_proposals(conn, propose_triage_rules(conn))
    except Exception:  # noqa: S110  # nosec B110
        # Swallow all exceptions — the watermark is always cleared.
        pass
    finally:
        set_watermark(conn, "triage_run:state", "idle")
        conn.close()


def _batch_op_running(state: str | None) -> bool:
    """Return whether *state* (the ``batch_op:state`` watermark) means running.

    "Running" is any value that is neither ``None`` nor the literal
    ``"idle"`` — i.e. the JSON progress payload set while a batch worker
    is in flight.
    """
    return state is not None and state != "idle"


def _release_batch_op(db_path: str) -> None:
    """Reset ``batch_op:state`` to ``"idle"`` so a later batch op can run."""
    from robotsix_auto_mail.db import init_db, set_watermark

    conn = init_db(db_path, skip_migrations=True)
    try:
        set_watermark(conn, "batch_op:state", "idle")
    finally:
        conn.close()


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
    )
    from robotsix_auto_mail.imap import _BATCH_UID_CHUNK, ImapClient

    conn = init_db(db_path, skip_migrations=True)
    try:
        records = _collect_records_for_action(conn, "TO_DELETE")
        total = len(records)
        set_watermark(
            conn,
            "batch_op:state",
            json.dumps({"op": "delete", "done": 0, "total": total}),
        )

        need_imap = mail_config is not None and any(
            r.imap_uid is not None for r in records
        )
        done = 0
        ctx: Any = (
            ImapClient(mail_config)
            if need_imap and mail_config is not None
            else contextlib.nullcontext()
        )
        with ctx as client:
            if client is not None and mail_config is not None:
                client.select_folder(mail_config.imap_folder)
            for start in range(0, total, _BATCH_UID_CHUNK):
                chunk = records[start : start + _BATCH_UID_CHUNK]
                uids = [r.imap_uid for r in chunk if r.imap_uid is not None]
                if client is not None and uids:
                    client.delete_messages(uids)
                for record in chunk:
                    delete_record_by_message_id(conn, record.message_id)
                conn.commit()
                done += len(chunk)
                set_watermark(
                    conn,
                    "batch_op:state",
                    json.dumps({"op": "delete", "done": done, "total": total}),
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
) -> None:
    """Archive every ``TO_ARCHIVE`` mail from IMAP + local DB in the background.

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
    )
    from robotsix_auto_mail.imap import ImapClient
    from robotsix_auto_mail.triage import get_archive_subfolder

    conn = init_db(db_path, skip_migrations=True)
    try:
        records = _collect_records_for_action(conn, "TO_ARCHIVE")
        total = len(records)
        set_watermark(
            conn,
            "batch_op:state",
            json.dumps({"op": "archive", "done": 0, "total": total}),
        )

        namespace = mail_config.archive_namespace if mail_config is not None else ""
        effective_root = namespace + archive_root
        done = 0

        need_imap = mail_config is not None and any(
            r.imap_uid is not None for r in records
        )
        if need_imap and mail_config is not None:
            with ImapClient(mail_config) as client:
                client.select_folder(mail_config.imap_folder)
                delimiter = next(
                    (f.delimiter for f in client.list_folders() if f.delimiter),
                    "/",
                )
                # Group records by their effective destination folder.
                groups: dict[str, list[MailRecord]] = {}
                for record in records:
                    subfolder = get_archive_subfolder(conn, record.message_id, record)
                    dest = _archive_dest_folder(effective_root, subfolder, delimiter)
                    if dest is None:
                        # Destination escapes the archive root — skip the
                        # record (left re-triggerable), mirroring triage.
                        continue
                    groups.setdefault(dest, []).append(record)

                for dest, group in groups.items():
                    uids = [r.imap_uid for r in group if r.imap_uid is not None]
                    if uids:
                        # Ensure the destination hierarchy exists.
                        parts = dest.split(delimiter)
                        for i in range(1, len(parts) + 1):
                            client.create_folder(delimiter.join(parts[:i]))
                        client.move_messages(uids, dest)
                    for record in group:
                        delete_record_by_message_id(conn, record.message_id)
                    conn.commit()
                    done += len(group)
                    set_watermark(
                        conn,
                        "batch_op:state",
                        json.dumps({"op": "archive", "done": done, "total": total}),
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
                    json.dumps({"op": "archive", "done": done, "total": total}),
                )
    except Exception:  # noqa: S110  # nosec B110
        # Swallow all exceptions — the watermark is always cleared.
        pass
    finally:
        set_watermark(conn, "batch_op:state", "idle")
        conn.close()
