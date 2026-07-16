"""Data-loading functions for the board server.

Extracted from ``board.py`` to keep the views module focused:
``_gather_account_board_data`` and its helper functions read
from the SQLite database and return raw structures consumed
by the orchestration layer in ``board.py``.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from typing import Any, cast

from robotsix_auto_mail.config import DEFAULT_ARCHIVE_ROOT
from robotsix_auto_mail.core._constants import (
    _BATCH_OP_STATE_KEY,
    _TRIAGE_RUN_STATE_KEY,
)
from robotsix_auto_mail.db import MailRecord, get_watermark, list_records
from robotsix_auto_mail.db.queries import get_account_health
from robotsix_auto_mail.server._constants import (
    _BOARD_COLUMNS,
    _parse_archive_structure,
    _with_db,
)
from robotsix_auto_mail.triage import (
    HUMAN_TRIAGE,
    INBOX,
    TO_ARCHIVE,
    TriageDecision,
    get_archive_subfolder,
    list_triage_decisions,
)


def _read_account_health(
    conn: sqlite3.Connection,
) -> tuple[dict[str, Any] | None, bool]:
    """Read account health and triage-running watermarks.

    Returns a ``(health, triage_running)`` pair.
    """
    health = get_account_health(conn)
    triage_running = get_watermark(conn, _TRIAGE_RUN_STATE_KEY) == "running"
    return health, triage_running


def _parse_batch_op(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Parse the batch-op watermark; return the progress dict or ``None``.

    The watermark value is ``"idle"`` / ``None`` when no batch op is
    running, else a JSON ``{"op", "done", "total"}`` progress payload.
    A bare ``"running"`` sentinel (set before the worker writes its first
    JSON payload) still counts as a running batch op with as-yet-unknown
    counts.
    """
    batch_raw = get_watermark(conn, _BATCH_OP_STATE_KEY)
    if batch_raw is None or batch_raw == "idle":
        return None
    try:
        parsed = json.loads(batch_raw)
    except json.JSONDecodeError, TypeError:
        parsed = None
    if isinstance(parsed, dict):
        return {
            "op": parsed.get("op"),
            "done": parsed.get("done"),
            "total": parsed.get("total"),
        }
    # Bare "running" sentinel.
    return {"op": None, "done": None, "total": None}


def _load_triage_state(
    conn: sqlite3.Connection,
) -> tuple[list[MailRecord], dict[str, TriageDecision], dict[str, list[MailRecord]]]:
    """Load every record and triage decision; bucket records by action.

    Returns ``(all_records, triage_by_mid, column_buckets)``.
    """
    all_records = list_records(conn)
    triage_by_mid: dict[str, TriageDecision] = {
        decision.message_id: decision for decision in list_triage_decisions(conn)
    }
    # Bucket records into columns by their triage-decision action.
    # Untriaged records land in the ``"INBOX"`` column.
    column_buckets: dict[str, list[MailRecord]] = {
        action: [] for action in _BOARD_COLUMNS
    }
    for record in all_records:
        decision = triage_by_mid.get(record.message_id)
        if decision is not None:
            column = decision.action
            # Guard: an unrecognised action lands in HUMAN_TRIAGE.
            if column not in column_buckets:
                column = HUMAN_TRIAGE
        else:
            column = INBOX
        column_buckets[column].append(record)
    return all_records, triage_by_mid, column_buckets


def _load_archive_context(
    conn: sqlite3.Connection,
    archive_root: str,
    column_buckets: dict[str, list[MailRecord]],
) -> dict[str, Any]:
    """Read archive-structure watermark, compute per-record subfolders,
    and return archive-related context.

    Also sorts the TO_ARCHIVE bucket in-place by destination subfolder
    so the board JS can render contiguous per-folder groups.

    Returns a dict with keys ``archive_subfolders``, ``folder_exists``,
    and ``archive_folders``.
    """
    # Read the archive_structure watermark to know which folders exist.
    archive_raw = get_watermark(conn, "archive_structure")
    existing_folders, delimiter, effective_root = _parse_archive_structure(
        archive_raw, archive_root
    )

    # Compute effective subfolder for each TO_ARCHIVE record.
    archive_subfolders: dict[str, str] = {}
    folder_exists: dict[str, bool] = {}
    for record in column_buckets.get(TO_ARCHIVE, []):
        subfolder = get_archive_subfolder(conn, record.message_id, record)
        archive_subfolders[record.message_id] = subfolder
        if subfolder:
            translated = subfolder.replace("/", delimiter)
            full_path = f"{effective_root}{delimiter}{translated}"
        else:
            full_path = effective_root
        folder_exists[record.message_id] = full_path in existing_folders

    # Order TO_ARCHIVE cards by destination so the board JS renders
    # contiguous per-folder groups (each with an "Archive these" button).
    # ``list.sort`` is stable, so the prior within-folder order is kept.
    to_archive_bucket = column_buckets.get(TO_ARCHIVE)
    if to_archive_bucket:
        to_archive_bucket.sort(key=lambda r: archive_subfolders.get(r.message_id, ""))

    # Existing archive subfolders (relative to the root) for the per-card
    # override dropdown — strip the ``<effective_root><delimiter>`` prefix
    # off each managed folder, dropping the root itself.
    _root_prefix = f"{effective_root}{delimiter}"
    archive_folders = sorted(
        name[len(_root_prefix) :]
        for name in existing_folders
        if name.startswith(_root_prefix) and name != effective_root
    )

    return {
        "archive_subfolders": archive_subfolders,
        "folder_exists": folder_exists,
        "archive_folders": archive_folders,
    }


def _load_unsubscribe_suggestions(
    conn: sqlite3.Connection,
) -> dict[str, dict[str, Any]]:
    """Read the unsubscribe-suggestions watermark.

    Returns a ``dict[str, dict[str, Any]]`` (empty dict if none present
    or if the watermark is malformed).
    """
    suggestions_raw = get_watermark(conn, "unsubscribe_suggestions")
    if suggestions_raw is None:
        return {}
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        return cast("dict[str, dict[str, Any]]", json.loads(suggestions_raw))
    return {}


def _build_record_notes_map(all_records: list[MailRecord]) -> dict[str, str]:
    """Build a ``{message_id: notes}`` map for records that have notes."""
    return {r.message_id: r.notes for r in all_records if r.notes}


def _gather_account_board_data(
    db_path: str,
    archive_root: str = DEFAULT_ARCHIVE_ROOT,
) -> dict[str, Any]:
    """Read one account's DB and return the raw structures for board building.

    Returns a dict with keys: ``triage_running``, ``batch_op``,
    ``triage_by_mid``, ``column_buckets``, ``proposals``,
    ``archive_subfolders``, ``folder_exists``, ``unsubscribe_suggestions``,
    ``record_notes``.

    This is the DB-reading half of :func:`_build_board_content`, extracted
    so the global board can call it per-account.
    """
    with _with_db(db_path, skip_migrations=True) as conn:
        health, triage_running = _read_account_health(conn)
        batch_op = _parse_batch_op(conn)
        all_records, triage_by_mid, column_buckets = _load_triage_state(conn)
        archive_ctx = _load_archive_context(conn, archive_root, column_buckets)
        unsubscribe_suggestions = _load_unsubscribe_suggestions(conn)
        record_notes = _build_record_notes_map(all_records)

    return {
        "triage_running": triage_running,
        "batch_op": batch_op,
        "health": health,
        "triage_by_mid": triage_by_mid,
        "column_buckets": column_buckets,
        **archive_ctx,
        "unsubscribe_suggestions": unsubscribe_suggestions,
        "record_notes": record_notes,
    }
