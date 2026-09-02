"""Tests for force-fetch operations (POST /force-fetch and background runner)."""

from __future__ import annotations

from unittest import mock

from pydantic import SecretStr

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import set_watermark
from tests.server.conftest_helpers import (
    _populate_db,
    _post_to_path,
    _start_test_server,
)

# ---------------------------------------------------------------------------
# POST /force-fetch tests
# ---------------------------------------------------------------------------


def test_force_fetch_endpoint_redirects(single_db: str) -> None:
    """POST /force-fetch returns 302 with Location: /board."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "test-msg",
                "sender": "x@x.com",
                "subject": "Test",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(port, "/force-fetch", {})
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"
    finally:
        server.shutdown()


def test_force_fetch_endpoint_idempotent(single_db: str) -> None:
    """POST /force-fetch is a no-op (302) when ingest_run:state is already 'running'."""
    from robotsix_auto_mail.db import get_watermark
    from robotsix_auto_mail.db import init_db as _init_db

    # Pre-set ingest_run:state to "running".
    conn = _init_db(single_db)
    try:
        set_watermark(conn, "ingest_run:state", "running")
    finally:
        conn.close()

    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(port, "/force-fetch", {})
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"

        # Watermark should still be "running" (no-op, no thread spawned).
        conn2 = _init_db(single_db)
        try:
            assert get_watermark(conn2, "ingest_run:state") == "running"
        finally:
            conn2.close()
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# _run_ingest_background tests
# ---------------------------------------------------------------------------


def test_force_fetch_background_no_mail_config_clears_watermark(
    single_db: str,
) -> None:
    """_run_ingest_background(db_path, None) returns cleanly, watermark is 'idle'."""
    from robotsix_auto_mail.db import get_watermark
    from robotsix_auto_mail.db import init_db as _init_db
    from robotsix_auto_mail.server.adapters import _run_ingest_background

    _init_db(single_db).close()

    # Should not raise.
    _run_ingest_background(single_db, None)

    conn = _init_db(single_db)
    try:
        state = get_watermark(conn, "ingest_run:state")
        assert state == "idle"
    finally:
        conn.close()


def test_force_fetch_background_runs_ingest_cycle(single_db: str) -> None:
    """_run_ingest_background calls _ingest_cycle with the right args and
    clears the watermark (real function body, dependency mocked)."""
    from robotsix_auto_mail.db import get_watermark
    from robotsix_auto_mail.db import init_db as _init_db
    from robotsix_auto_mail.server.adapters import _run_ingest_background

    _init_db(single_db).close()

    config = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password=SecretStr("secret"),
        db_path=single_db,
    )

    with mock.patch(
        "robotsix_auto_mail.cli.commands_ingest._ingest_cycle"
    ) as mock_cycle:
        _run_ingest_background(single_db, config)

    mock_cycle.assert_called_once_with(config, dry_run=False, db_path=single_db)

    conn = _init_db(single_db)
    try:
        state = get_watermark(conn, "ingest_run:state")
        assert state == "idle"
    finally:
        conn.close()
