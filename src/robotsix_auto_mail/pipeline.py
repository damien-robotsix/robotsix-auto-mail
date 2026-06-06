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

from robotsix_auto_mail.archive import setup_archive
from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import insert_record, record_exists
from robotsix_auto_mail.fetch import fetch_new_messages, update_watermark
from robotsix_auto_mail.imap import ImapClient
from robotsix_auto_mail.parser import parse_message
from robotsix_auto_mail.triage import run_triage_agent

_logger = logging.getLogger(__name__)


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
    """

    total_fetched: int
    stored: int
    skipped: int
    errors: list[IngestError]
    triaged: int = 0


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
    if not dry_run and config.archive_enabled:
        try:
            setup_archive(
                db_conn,
                imap_client,
                archive_root=config.archive_root,
                api_key=config.llm_api_key,
            )
        except Exception:
            _logger.exception("Archive setup failed; continuing ingestion")

    # 1. Fetch raw messages (read-only on DB).
    messages = fetch_new_messages(db_conn, imap_client, config)
    total_fetched = len(messages)

    if total_fetched == 0:
        return IngestResult(
            total_fetched=0, stored=0, skipped=0, errors=[]
        )

    # 2. Process each message.
    stored = 0
    skipped = 0
    errors: list[IngestError] = []
    max_uid: int = 0

    for uid, raw_bytes in messages:
        # Track the highest UID seen in this batch.
        if uid > max_uid:
            max_uid = uid

        # -- Parse -----------------------------------------------------------
        try:
            record = parse_message(raw_bytes, imap_uid=uid)
        except Exception as exc:
            errors.append(
                IngestError(
                    uid=uid,
                    message_id="",
                    error=str(exc) if str(exc) else repr(exc),
                )
            )
            continue

        # -- Deduplication check ---------------------------------------------
        if record_exists(db_conn, record.message_id):
            skipped += 1
            continue

        # -- Store (skip in dry-run) -----------------------------------------
        if dry_run:
            stored += 1
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
            continue

        if rowid is not None:
            stored += 1
        else:
            # Belts-and-suspenders: record_exists said False but insert
            # still returned None (race / concurrent writer).  Count as
            # skipped.
            skipped += 1

    # 3. Advance watermark to the highest UID seen (skip in dry-run).
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
                only_undecided=True,
            )
            triaged = len(decisions)
        except Exception:
            _logger.exception("Triage failed; continuing ingestion")

    return IngestResult(
        total_fetched=total_fetched,
        stored=stored,
        skipped=skipped,
        errors=errors,
        triaged=triaged,
    )
