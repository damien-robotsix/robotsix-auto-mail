"""Tests for ingest_mail first-run archive setup and post-ingest triage pass."""

from __future__ import annotations

import sqlite3
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.pipeline import (
    IngestResult,
    ingest_mail,
)
from tests.pipeline._helpers import _make_raw_message, _mock_imap_client


# ---------------------------------------------------------------------------
# ingest_mail - first-run archive setup
# ---------------------------------------------------------------------------


@mock.patch("robotsix_auto_mail.pipeline.setup_archive")
@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_calls_setup_archive_before_fetch(
    mock_fetch: mock.MagicMock,
    mock_setup_archive: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """A normal run calls setup_archive exactly once, before fetching."""
    manager = mock.Mock()
    manager.attach_mock(mock_setup_archive, "setup_archive")
    manager.attach_mock(mock_fetch, "fetch_new_messages")
    mock_fetch.return_value = []

    imap = _mock_imap_client()
    ingest_mail(conn, imap, cfg)

    mock_setup_archive.assert_called_once_with(
        conn,
        imap,
        archive_root=cfg.archive_root,
        api_key=cfg.llm_api_key.get_secret_value(),
        provider_model=cfg.llm_provider_model,
    )
    # setup_archive must run before fetch_new_messages.
    call_order = [c[0] for c in manager.mock_calls]
    assert call_order.index("setup_archive") < call_order.index("fetch_new_messages")


@mock.patch("robotsix_auto_mail.pipeline.setup_archive")
@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_dry_run_does_not_call_setup_archive(
    mock_fetch: mock.MagicMock,
    mock_setup_archive: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """dry_run=True must not call setup_archive."""
    mock_fetch.return_value = []
    imap = _mock_imap_client()

    ingest_mail(conn, imap, cfg, dry_run=True)

    mock_setup_archive.assert_not_called()


@mock.patch("robotsix_auto_mail.pipeline.setup_archive")
@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_archive_disabled_does_not_call_setup_archive(
    mock_fetch: mock.MagicMock,
    mock_setup_archive: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """archive_enabled=False must skip setup_archive entirely."""
    cfg_disabled = cfg.model_copy(update={"archive_enabled": False})
    mock_fetch.return_value = []
    imap = _mock_imap_client()

    ingest_mail(conn, imap, cfg_disabled)

    mock_setup_archive.assert_not_called()


@mock.patch("robotsix_auto_mail.pipeline.setup_archive")
@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_passes_configured_archive_root(
    mock_fetch: mock.MagicMock,
    mock_setup_archive: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """The configured archive_root is forwarded to setup_archive."""
    cfg_custom = cfg.model_copy(update={"archive_root": "custom-archive"})
    mock_fetch.return_value = []
    imap = _mock_imap_client()

    ingest_mail(conn, imap, cfg_custom)

    mock_setup_archive.assert_called_once_with(
        conn,
        imap,
        archive_root="custom-archive",
        api_key=cfg.llm_api_key.get_secret_value(),
        provider_model=cfg.llm_provider_model,
    )


@mock.patch("robotsix_auto_mail.pipeline.setup_archive")
@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_setup_archive_failure_does_not_propagate(
    mock_fetch: mock.MagicMock,
    mock_setup_archive: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """An exception from setup_archive is swallowed; ingestion continues."""
    mock_setup_archive.side_effect = RuntimeError("LLM exploded")
    mock_fetch.return_value = [
        (1, _make_raw_message(message_id="<a@x>")),
    ]
    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg)

    assert isinstance(result, IngestResult)
    assert result.total_fetched == 1
    assert result.stored == 1


# ---------------------------------------------------------------------------
# ingest_mail - post-ingest triage pass
# ---------------------------------------------------------------------------


@mock.patch("robotsix_auto_mail.pipeline.run_triage_agent")
@mock.patch("robotsix_auto_mail.pipeline.setup_archive")
@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_runs_triage_on_new_mail(
    mock_fetch: mock.MagicMock,
    mock_setup_archive: mock.MagicMock,
    mock_triage: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """A normal run triages only-undecided mail and reports the count."""
    mock_fetch.return_value = [(1, _make_raw_message(message_id="<a@x>"))]
    mock_triage.return_value = [object(), object()]
    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg)

    mock_triage.assert_called_once_with(
        conn,
        api_key=cfg.llm_api_key.get_secret_value(),
        provider_model=cfg.llm_provider_model,
        only_undecided=True,
        user_email=cfg.username,
        rules_path=mock.ANY,
    )
    assert result.triaged == 2
    # Triage must perform no IMAP/mailbox action of its own.
    imap.assert_not_called()


@mock.patch("robotsix_auto_mail.pipeline.run_triage_agent")
@mock.patch("robotsix_auto_mail.pipeline.setup_archive")
@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_triage_disabled_does_not_call_triage(
    mock_fetch: mock.MagicMock,
    mock_setup_archive: mock.MagicMock,
    mock_triage: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """triage_on_ingest=False must skip run_triage_agent entirely."""
    cfg_disabled = cfg.model_copy(update={"triage_on_ingest": False})
    mock_fetch.return_value = [(1, _make_raw_message(message_id="<a@x>"))]
    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg_disabled)

    mock_triage.assert_not_called()
    assert result.triaged == 0


@mock.patch("robotsix_auto_mail.pipeline.run_triage_agent")
@mock.patch("robotsix_auto_mail.pipeline.setup_archive")
@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_dry_run_does_not_call_triage(
    mock_fetch: mock.MagicMock,
    mock_setup_archive: mock.MagicMock,
    mock_triage: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """dry_run=True must not call run_triage_agent."""
    mock_fetch.return_value = [(1, _make_raw_message(message_id="<a@x>"))]
    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg, dry_run=True)

    mock_triage.assert_not_called()
    assert result.triaged == 0


@mock.patch("robotsix_auto_mail.pipeline.run_triage_agent")
@mock.patch("robotsix_auto_mail.pipeline.setup_archive")
@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_triage_failure_does_not_propagate(
    mock_fetch: mock.MagicMock,
    mock_setup_archive: mock.MagicMock,
    mock_triage: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """A triage exception is swallowed; ingestion still returns triaged=0."""
    from robotsix_auto_mail.triage import TriageError

    mock_fetch.return_value = [(1, _make_raw_message(message_id="<a@x>"))]
    mock_triage.side_effect = TriageError("LLM exploded")
    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg)

    assert isinstance(result, IngestResult)
    assert result.total_fetched == 1
    assert result.stored == 1
    assert result.triaged == 0
