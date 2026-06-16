"""Pipeline orchestration: fetch → parse → store → watermark.

Wires together the three independent layers — IMAP fetch, MIME parse,
and local datastore — into a single ``ingest_mail`` call.  Processes
messages one at a time, collects errors, skips duplicates idempotently,
and advances the watermark only after the full batch.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import time

import structlog

from robotsix_auto_mail.archive import setup_archive
from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import (
    get_watermark,
    insert_record,
    record_exists,
    set_watermark,
)
from robotsix_auto_mail.imap import ImapClient
from robotsix_auto_mail.parser import parse_message
from robotsix_auto_mail.triage import run_triage_agent

_logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Watermark-aware IMAP fetch helpers (inlined from former fetch.py)
# ---------------------------------------------------------------------------

_WATERMARK_KEY = "imap_uid"


def fetch_new_messages(
    conn: sqlite3.Connection,
    client: ImapClient,
    config: MailConfig,
) -> list[tuple[int, bytes]]:
    """Fetch raw messages with UIDs beyond the stored watermark.

    Reads the ``"imap_uid"`` watermark from *conn*, selects
    ``config.imap_folder``, searches for UIDs strictly greater than the
    watermark (or ``"ALL"`` on first run), and fetches their raw MIME
    bytes via ``BODY.PEEK[]``.

    This function is **read-only** on the DB: it reads the watermark
    but does not update it.  The caller (pipeline) wraps fetch → parse →
    insert → update-watermark in a single transaction for atomicity.

    Args:
        conn: An open ``sqlite3.Connection`` to the local datastore.
        client: A connected ``ImapClient``.
        config: Mail configuration whose ``imap_folder`` determines
            which mailbox to select.

    Returns:
        A (possibly empty) list of ``(uid, raw_mime_bytes)`` pairs for
        messages that are newer than the stored watermark.
    """
    # 1. Read watermark.
    watermark_raw = get_watermark(conn, _WATERMARK_KEY)

    # 2. Select the configured folder.
    client.select_folder(config.imap_folder)

    # 3. Build search criteria.
    if watermark_raw is not None:
        criteria = f"UID {watermark_raw}:*"
    else:
        criteria = "ALL"

    # 4. Search.
    uids = client.search_uids(criteria)

    # 5. Filter out the watermark UID itself (IMAP ``UID N:*`` is
    #    inclusive).
    if watermark_raw is not None:
        try:
            watermark_uid = int(watermark_raw)
        except ValueError, TypeError:
            watermark_uid = None
        if watermark_uid is not None:
            uids = [u for u in uids if u > watermark_uid]

    # 6. No new UIDs → nothing to fetch.
    if not uids:
        return []

    # 7. Fetch message bodies.
    return client.fetch_messages(uids)


def update_watermark(conn: sqlite3.Connection, uid: int) -> None:
    """Persist the last-seen IMAP UID so the next run only fetches newer mail.

    Thin wrapper around ``set_watermark`` with the hardcoded key
    ``"imap_uid"``.
    """
    set_watermark(conn, _WATERMARK_KEY, str(uid))


@dataclasses.dataclass(frozen=True)
class IngestError:
    """A single failed message during ingestion.

    Attributes:
        uid: IMAP UID of the failing message.
        message_id: Parsed ``Message-ID`` header (may be ``""`` if
            parsing failed before the header was extracted).
        error: Human-readable error description.
    """

    uid: int
    message_id: str
    error: str


@dataclasses.dataclass(frozen=True)
class IngestResult:
    """Summary returned by ``ingest_mail``.

    Attributes:
        total_fetched: Number of messages returned by
            ``fetch_new_messages``.
        stored: Number of messages newly inserted into
            ``mail_records``.
        skipped: Number of messages whose ``message_id`` was
            already present in the database.
        errors: Per-message failures (parse errors, DB write
            errors, etc.).
        triaged: Number of triage decisions produced by the
            automatic post-ingest triage pass (0 when triage is
            disabled, dry-run, or raised).
        duration_ms: Wall-clock duration of the ingestion run in
            milliseconds (monotonic ``time.perf_counter`` delta).
    """

    total_fetched: int
    stored: int
    skipped: int
    errors: list[IngestError]
    triaged: int = 0
    duration_ms: float = 0.0


def _process_messages(
    db_conn: sqlite3.Connection,
    messages: list[tuple[int, bytes]],
    *,
    dry_run: bool = False,
    source_folder: str = "INBOX",
) -> tuple[int, int, list[IngestError]]:
    """Parse, dedup, and store a batch of raw ``(uid, bytes)`` messages.

    Shared per-message body used by ``ingest_mail``:
    parses via ``parse_message``, dedups via
    ``record_exists`` (skipping duplicates by ``message_id``), inserts
    new records via ``insert_record``, and collects per-message
    ``IngestError``s.  Watermark handling is intentionally *not* done
    here — that is the caller's concern.

    When *source_folder* is provided and a duplicate ``message_id`` is
    found (``record_exists`` returns ``True``), the existing row's
    ``source_folder`` and ``imap_uid`` are **updated** rather than
    skipped — this keeps re-ingested records actionable
    even when the tracked UID has become stale.

    Returns:
        A ``(stored, skipped, errors)`` tuple.
    """
    stored = 0
    skipped = 0
    errors: list[IngestError] = []

    for uid, raw_bytes in messages:
        # -- Parse -----------------------------------------------------------
        try:
            record = parse_message(raw_bytes, imap_uid=uid, source_folder=source_folder)
        except Exception as exc:
            errors.append(
                IngestError(
                    uid=uid,
                    message_id="",
                    error=str(exc) if str(exc) else repr(exc),
                )
            )
            _logger.debug(
                "message_processing",
                uid=uid,
                message_id="",
                action="error",
                error=str(exc) if str(exc) else repr(exc),
            )
            continue

        # -- Deduplication check ---------------------------------------------
        if record_exists(db_conn, record.message_id):
            # When a duplicate is found, refresh the existing row's
            # source_folder + UID so legacy mails re-ingested from a
            # named folder become actionable for archive/delete.
            from robotsix_auto_mail.db import update_record_source

            update_record_source(
                db_conn,
                record.message_id,
                source_folder=source_folder,
                imap_uid=uid,
            )
            skipped += 1
            _logger.debug(
                "message_processing",
                uid=uid,
                message_id=record.message_id,
                action="skipped",
            )
            continue

        # -- Store (skip in dry-run) -----------------------------------------
        if dry_run:
            stored += 1
            _logger.debug(
                "message_processing",
                uid=uid,
                message_id=record.message_id,
                action="stored",
            )
            continue

        try:
            rowid = insert_record(db_conn, record)
        except Exception as exc:
            errors.append(
                IngestError(
                    uid=uid,
                    message_id=record.message_id,
                    error=str(exc) if str(exc) else repr(exc),
                )
            )
            _logger.debug(
                "message_processing",
                uid=uid,
                message_id=record.message_id,
                action="error",
                error=str(exc) if str(exc) else repr(exc),
            )
            continue

        if rowid is not None:
            stored += 1
            _logger.debug(
                "message_processing",
                uid=uid,
                message_id=record.message_id,
                action="stored",
            )
        else:
            # Belts-and-suspenders: record_exists said False but insert
            # still returned None (race / concurrent writer).  Count as
            # skipped.
            skipped += 1
            _logger.debug(
                "message_processing",
                uid=uid,
                message_id=record.message_id,
                action="skipped",
            )

    return stored, skipped, errors


def ingest_mail(
    db_conn: sqlite3.Connection,
    imap_client: ImapClient,
    config: MailConfig,
    *,
    dry_run: bool = False,
) -> IngestResult:
    """Run the full ingestion pipeline: fetch → parse → store → watermark.

    Parameters
    ----------
    db_conn:
        An open ``sqlite3.Connection`` to the local datastore.
    imap_client:
        A connected ``ImapClient`` (already entered via context manager).
    config:
        Mail configuration (used by ``fetch_new_messages``).
    dry_run:
        When ``True``, messages are fetched and parsed but
        ``insert_record`` and ``update_watermark`` are skipped.
        The ``stored`` count reflects messages that *would have been*
        inserted (i.e. ``record_exists`` returned ``False``).

    Returns
    -------
    IngestResult
        Summary with total fetched, stored, skipped, and any errors.
    """
    # 0. First-run archive setup (best-effort; skipped in dry-run).
    #    Creating folders / writing the watermark must not happen on a
    #    dry run, and any archive failure (LLM/network/IMAP) must not
    #    abort ingestion — setup_archive only persists its watermark on
    #    success, so a failed run naturally retries next time.
    _logger.info(
        "ingest_begin",
        dry_run=dry_run,
        archive_enabled=config.archive_enabled,
        triage_on_ingest=config.triage_on_ingest,
    )
    _t0 = time.perf_counter()
    if not dry_run and config.archive_enabled:
        try:
            setup_archive(
                db_conn,
                imap_client,
                archive_root=config.archive_root,
                archive_namespace=config.archive_namespace,
                api_key=config.llm_api_key,
                provider=config.llm_provider,
            )
            _logger.info("archive_setup_done")
        except Exception:
            _logger.exception("archive_setup_failed")

    # 1. Fetch raw messages (read-only on DB).
    messages = fetch_new_messages(db_conn, imap_client, config)
    total_fetched = len(messages)
    _logger.debug("fetch_done", count=total_fetched)

    if total_fetched == 0:
        return IngestResult(total_fetched=0, stored=0, skipped=0, errors=[])

    # 2. Process each message.
    stored, skipped, errors = _process_messages(
        db_conn, messages, dry_run=dry_run, source_folder=config.imap_folder
    )

    # 3. Advance watermark to the highest UID seen (skip in dry-run).
    max_uid = max((uid for uid, _ in messages), default=0)
    if max_uid > 0 and not dry_run:
        update_watermark(db_conn, max_uid)

    # 4. Triage newly-stored inbox mail (best-effort; skipped in dry-run
    #    and when disabled via config).  Only undecided inbox records are
    #    triaged so re-running ingest produces no duplicate decisions and
    #    no extra LLM calls.  A triage failure must never abort ingestion
    #    or change the stored/skipped counts — mirror the setup_archive
    #    best-effort precedent.
    triaged = 0
    if not dry_run and config.triage_on_ingest:
        try:
            decisions = run_triage_agent(
                db_conn,
                api_key=config.llm_api_key,
                provider=config.llm_provider,
                only_undecided=True,
                user_email=config.username,
            )
            triaged = len(decisions)
            _logger.info("triage_done", decisions=triaged)
        except Exception:
            _logger.exception("triage_failed")

    duration_ms = round((time.perf_counter() - _t0) * 1000, 1)
    _logger.info(
        "batch_summary",
        total_fetched=total_fetched,
        stored=stored,
        skipped=skipped,
        error_count=len(errors),
        triaged=triaged,
        duration_ms=duration_ms,
    )

    return IngestResult(
        total_fetched=total_fetched,
        stored=stored,
        skipped=skipped,
        errors=errors,
        triaged=triaged,
        duration_ms=duration_ms,
    )


def reconcile_records(
    db_conn: sqlite3.Connection,
    imap_client: ImapClient,
) -> tuple[int, int]:
    """Verify tracked records' UIDs; heal moved mails, remove deleted ones.

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
            # Select the folder — skip on error (e.g. folder was deleted).
            try:
                imap_client.select_folder(folder)
            except ImapError:
                _logger.warning("reconcile_select_error", folder=folder)
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
                    _logger.warning("reconcile_search_error", folder=folder)
                    folder_failed = True
                    break

            if folder_failed:
                continue  # Skip this folder entirely on transient error.

            missing_uids = tracked_uids - found_uids

            # 4. For each missing UID, cross-folder resolve to heal or remove.
            for missing_uid in missing_uids:
                message_id = uid_map[missing_uid]
                try:
                    resolved = cross_folder_resolve(imap_client, message_id)
                except ImapError:
                    _logger.warning("reconcile_resolve_error", message_id=message_id)
                    continue

                if resolved is not None:
                    new_folder, new_uid = resolved
                    update_record_source(
                        db_conn,
                        message_id,
                        source_folder=new_folder,
                        imap_uid=new_uid,
                    )
                    db_conn.commit()
                    healed += 1
                else:
                    delete_record_by_message_id(db_conn, message_id)
                    db_conn.commit()
                    removed += 1

        return (healed, removed)

    except ImapError as exc:
        _logger.warning("reconcile_imap_error", error=str(exc))
        return (0, 0)
