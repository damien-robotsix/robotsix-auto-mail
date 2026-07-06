"""Tests for UIDVALIDITY reconciliation in the ingest pipeline.

IMAP UIDs are only monotonic within a given UIDVALIDITY. When the server
renumbers UIDs, the stored ``imap_uid`` watermark becomes meaningless and the
incremental ``UID <wm>:*`` search silently returns nothing — stranding all new
mail. ``_reconcile_uidvalidity`` detects the change and clears the stale UID
watermark so the next fetch falls back to a full ``ALL`` scan.
"""

from __future__ import annotations

import sqlite3
from unittest import mock

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import get_watermark, set_watermark
from robotsix_auto_mail.pipeline import _reconcile_uidvalidity, ingest_mail
from tests.pipeline._helpers import _make_raw_message, _mock_imap_client

_UIDV = "imap_uidvalidity"
_UID = "imap_uid"


# ---------------------------------------------------------------------------
# _reconcile_uidvalidity
# ---------------------------------------------------------------------------


def test_first_run_returns_current_and_touches_nothing(
    conn: sqlite3.Connection, cfg: MailConfig
) -> None:
    """No stored UIDVALIDITY → returns current, leaves the (absent) watermark."""
    client = _mock_imap_client()
    client.select_folder_and_uidvalidity.return_value = (5, 111)

    result = _reconcile_uidvalidity(conn, client, cfg)

    assert result == 111
    assert get_watermark(conn, _UID) is None
    client.select_folder_and_uidvalidity.assert_called_once_with("INBOX")


def test_unchanged_preserves_uid_watermark(
    conn: sqlite3.Connection, cfg: MailConfig
) -> None:
    """Stored UIDVALIDITY == current → UID watermark is preserved."""
    set_watermark(conn, _UIDV, "111")
    set_watermark(conn, _UID, "50")
    client = _mock_imap_client()
    client.select_folder_and_uidvalidity.return_value = (5, 111)

    result = _reconcile_uidvalidity(conn, client, cfg)

    assert result == 111
    assert get_watermark(conn, _UID) == "50"


def test_change_clears_uid_watermark(conn: sqlite3.Connection, cfg: MailConfig) -> None:
    """Stored UIDVALIDITY differs → the stale UID watermark is deleted."""
    set_watermark(conn, _UIDV, "111")
    set_watermark(conn, _UID, "5000")
    client = _mock_imap_client()
    client.select_folder_and_uidvalidity.return_value = (3, 222)

    result = _reconcile_uidvalidity(conn, client, cfg)

    assert result == 222
    # Cleared → next fetch_new_messages falls back to an ALL scan.
    assert get_watermark(conn, _UID) is None


def test_dry_run_never_writes(conn: sqlite3.Connection, cfg: MailConfig) -> None:
    """A changed UIDVALIDITY on a dry run must not touch the watermark."""
    set_watermark(conn, _UIDV, "111")
    set_watermark(conn, _UID, "5000")
    client = _mock_imap_client()
    client.select_folder_and_uidvalidity.return_value = (3, 222)

    result = _reconcile_uidvalidity(conn, client, cfg, dry_run=True)

    assert result == 222
    assert get_watermark(conn, _UID) == "5000"  # untouched


def test_missing_uidvalidity_is_noop(conn: sqlite3.Connection, cfg: MailConfig) -> None:
    """Server doesn't advertise UIDVALIDITY → return None, touch nothing."""
    set_watermark(conn, _UID, "5000")
    client = _mock_imap_client()
    client.select_folder_and_uidvalidity.return_value = (3, None)

    result = _reconcile_uidvalidity(conn, client, cfg)

    assert result is None
    assert get_watermark(conn, _UID) == "5000"


# ---------------------------------------------------------------------------
# ingest_mail integration
# ---------------------------------------------------------------------------


@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_reingests_new_low_uid_mail_after_uidvalidity_change(
    mock_fetch: mock.MagicMock, conn: sqlite3.Connection, cfg: MailConfig
) -> None:
    """The bug fix: after a UIDVALIDITY change, low-UID new mail is ingested.

    Prior state: a high UID watermark (5000) from the old namespace. The server
    renumbered (new UIDVALIDITY), and new mail now has UID 1 — which the old
    ``UID 5000:*`` search would have missed. Reconciliation clears the stale
    watermark so the message is ingested, and both watermarks end consistent.
    """
    set_watermark(conn, _UIDV, "111")
    set_watermark(conn, _UID, "5000")
    imap = _mock_imap_client()
    imap.select_folder_and_uidvalidity.return_value = (1, 222)  # changed
    mock_fetch.return_value = [(1, _make_raw_message(message_id="<new@x>"))]

    result = ingest_mail(conn, imap, cfg)

    assert result.stored == 1
    # UID watermark was reset (5000 → cleared) then advanced to the new max (1).
    assert get_watermark(conn, _UID) == "1"
    # New UIDVALIDITY persisted so subsequent runs go incremental again.
    assert get_watermark(conn, _UIDV) == "222"


@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_persists_uidvalidity_on_first_run(
    mock_fetch: mock.MagicMock, conn: sqlite3.Connection, cfg: MailConfig
) -> None:
    """First successful ingest records the UIDVALIDITY alongside the UID."""
    imap = _mock_imap_client()
    imap.select_folder_and_uidvalidity.return_value = (1, 333)
    mock_fetch.return_value = [(7, _make_raw_message(message_id="<a@x>"))]

    ingest_mail(conn, imap, cfg)

    assert get_watermark(conn, _UID) == "7"
    assert get_watermark(conn, _UIDV) == "333"


@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_dry_run_persists_no_watermarks(
    mock_fetch: mock.MagicMock, conn: sqlite3.Connection, cfg: MailConfig
) -> None:
    """A dry run neither advances the UID watermark nor records UIDVALIDITY."""
    imap = _mock_imap_client()
    imap.select_folder_and_uidvalidity.return_value = (1, 444)
    mock_fetch.return_value = [(9, _make_raw_message(message_id="<b@x>"))]

    ingest_mail(conn, imap, cfg, dry_run=True)

    assert get_watermark(conn, _UID) is None
    assert get_watermark(conn, _UIDV) is None
