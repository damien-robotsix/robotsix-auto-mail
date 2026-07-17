"""Pipeline orchestration: fetch → parse → store → watermark.

Wires together the three independent layers — IMAP fetch, MIME parse,
and local datastore — into a single ``ingest_mail`` call.  Processes
messages one at a time, collects errors, skips duplicates idempotently,
and advances the watermark only after the full batch.
"""

from __future__ import annotations

import dataclasses
import logging
import sqlite3
import time

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import (
    delete_watermark,
    get_watermark,
    insert_record,
    record_exists,
    set_watermark,
)
from robotsix_auto_mail.db.archive import setup_archive
from robotsix_auto_mail.imap import ImapClient
from robotsix_auto_mail.pipeline._parse import ParseError as ParseError
from robotsix_auto_mail.pipeline._parse import parse_message
from robotsix_auto_mail.pipeline.reconcile import reconcile_records
from robotsix_auto_mail.triage import resolve_rules_path, run_triage_agent

_logger = logging.getLogger(__name__)

__all__ = [
    "IngestError",
    "IngestResult",
    "ParseError",
    "fetch_new_messages",
    "ingest_mail",
    "parse_message",
    "reconcile_records",
    "update_watermark",
]

# ---------------------------------------------------------------------------
# Watermark-aware IMAP fetch helpers (inlined from former fetch.py)
# ---------------------------------------------------------------------------

_WATERMARK_KEY = "imap_uid"
_UIDVALIDITY_KEY = "imap_uidvalidity"


def _reconcile_uidvalidity(
    conn: sqlite3.Connection,
    client: ImapClient,
    config: MailConfig,
    *,
    dry_run: bool = False,
) -> int | None:
    """Detect a server ``UIDVALIDITY`` change and reset the stale UID watermark.

    IMAP UIDs are only monotonic *within a given ``UIDVALIDITY``* (RFC 3501
    §2.3.1.1). When the server renumbers UIDs — mailbox recreated/restored,
    some Exchange/Dovecot maintenance — the stored ``"imap_uid"`` watermark
    points into the old namespace, so the incremental search ``UID <wm>:*``
    silently returns nothing and **all new mail stops being ingested**.

    This selects the folder, reads its current ``UIDVALIDITY``, and — when it
    differs from the value stored on the previous run — deletes the
    ``"imap_uid"`` watermark so :func:`fetch_new_messages` falls back to a full
    ``ALL`` scan (duplicates are dropped idempotently downstream by the
    ``message_id`` uniqueness check). Returns the current ``UIDVALIDITY`` so
    the caller can persist it alongside the new UID watermark once ingest
    succeeds.

    No DB writes occur on a dry run, or when the server does not advertise a
    parseable ``UIDVALIDITY`` (``None`` returned — the caller then leaves both
    watermarks untouched).
    """
    _count, current = client.select_folder_and_uidvalidity(config.imap_folder)
    if current is None:
        return None
    stored = get_watermark(conn, _UIDVALIDITY_KEY)
    if stored is not None and stored != str(current) and not dry_run:
        _logger.warning(
            "imap_uidvalidity_changed old=%s new=%s folder=%s — clearing UID "
            "watermark to re-scan the mailbox",
            stored,
            current,
            config.imap_folder,
        )
        delete_watermark(conn, _WATERMARK_KEY)
    return current


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
    criteria = f"UID {watermark_raw}:*" if watermark_raw is not None else "ALL"

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
                "message_processing uid=%s message_id= action=error error=%s",
                uid,
                str(exc) if str(exc) else repr(exc),
            )
            continue

        # -- Deduplication check ---------------------------------------------
        if record_exists(db_conn, record.message_id):
            # When a duplicate is found, refresh the existing row's
            # source_folder + UID so legacy mails re-ingested from a
            # named folder become actionable for archive/delete.
            # Suppress the write on dry runs — no commits must occur.
            if not dry_run:
                from robotsix_auto_mail.db import update_record_source

                update_record_source(
                    db_conn,
                    record.message_id,
                    source_folder=source_folder,
                    imap_uid=uid,
                )
            skipped += 1
            _logger.debug(
                "message_processing uid=%s message_id=%s action=skipped",
                uid,
                record.message_id,
            )
            continue

        # -- Store (skip in dry-run) -----------------------------------------
        if dry_run:
            stored += 1
            _logger.debug(
                "message_processing uid=%s message_id=%s action=stored",
                uid,
                record.message_id,
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
                "message_processing uid=%s message_id=%s action=error error=%s",
                uid,
                record.message_id,
                str(exc) if str(exc) else repr(exc),
            )
            continue

        if rowid is not None:
            stored += 1
            _logger.debug(
                "message_processing uid=%s message_id=%s action=stored",
                uid,
                record.message_id,
            )
        else:
            # Belts-and-suspenders: record_exists said False but insert
            # still returned None (race / concurrent writer).  Count as
            # skipped.
            skipped += 1
            _logger.debug(
                "message_processing uid=%s message_id=%s action=skipped",
                uid,
                record.message_id,
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
        "ingest_begin dry_run=%s archive_enabled=%s triage_on_ingest=%s",
        dry_run,
        config.archive_enabled,
        config.triage_on_ingest,
    )
    _t0 = time.perf_counter()
    if not dry_run and config.archive_enabled:
        try:
            setup_archive(
                db_conn,
                imap_client,
                archive_root=config.archive_root,
                api_key=config.llm_api_key,
                provider_model=config.llm_provider_model,
            )
            _logger.info("archive_setup_done")
        except Exception:
            _logger.exception("archive_setup_failed")

    # 0.6 UIDVALIDITY guard: if the server renumbered UIDs, the stored
    #     "imap_uid" watermark is meaningless — reset it so this run re-scans
    #     the folder (dedup by message_id keeps re-ingestion idempotent).
    #     Returns the current UIDVALIDITY to persist once ingest succeeds.
    current_uidvalidity = _reconcile_uidvalidity(
        db_conn, imap_client, config, dry_run=dry_run
    )

    # 1. Fetch raw messages (read-only on DB).
    messages = fetch_new_messages(db_conn, imap_client, config)
    total_fetched = len(messages)
    _logger.debug("fetch_done count=%s", total_fetched)

    if total_fetched == 0:
        return IngestResult(total_fetched=0, stored=0, skipped=0, errors=[])

    # 2. Process each message.
    stored, skipped, errors = _process_messages(
        db_conn, messages, dry_run=dry_run, source_folder=config.imap_folder
    )

    # 3. Advance watermark to the highest UID seen, and persist the current
    #    UIDVALIDITY alongside it so the pair stays consistent (skip in
    #    dry-run).
    max_uid = max((uid for uid, _ in messages), default=0)
    if not dry_run:
        if max_uid > 0:
            update_watermark(db_conn, max_uid)
        if current_uidvalidity is not None:
            set_watermark(db_conn, _UIDVALIDITY_KEY, str(current_uidvalidity))

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
                provider_model=config.llm_provider_model,
                only_undecided=True,
                user_email=config.username,
                rules_path=resolve_rules_path(
                    db_path=config.db_path, rules_path=config.triage_rules_path
                ),
            )
            triaged = len(decisions)
            _logger.info("triage_done decisions=%s", triaged)
        except Exception:
            _logger.exception("triage_failed")

    duration_ms = round((time.perf_counter() - _t0) * 1000, 1)
    _logger.info(
        "batch_summary total_fetched=%s stored=%s skipped=%s "
        "error_count=%s triaged=%s duration_ms=%s",
        total_fetched,
        stored,
        skipped,
        len(errors),
        triaged,
        duration_ms,
    )

    return IngestResult(
        total_fetched=total_fetched,
        stored=stored,
        skipped=skipped,
        errors=errors,
        triaged=triaged,
        duration_ms=duration_ms,
    )
