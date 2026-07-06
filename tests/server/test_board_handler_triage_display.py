"""Tests for the board handler (HTTP request routing and board rendering)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from robotsix_auto_mail.db import init_db
from tests.server.conftest import (
    _populate_db,
    _post_form,
    _start_test_server,
)

# ---------------------------------------------------------------------------
# Triage decision display (read-only badge + detail field)
# ---------------------------------------------------------------------------


def test_move_creates_triage_decision(single_db: str) -> None:
    """POST /move creates a triage_decisions row and does NOT update
    mail_records.status."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "move-triage",
                "sender": "x@x.com",
                "subject": "Move triage test",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        status, _body = _post_form(
            port, {"message_id": "move-triage", "triage_action": "TO_ARCHIVE"}
        )
        assert status == 302
    finally:
        server.shutdown()

    # Verify triage_decisions row was created.
    from robotsix_auto_mail.triage import get_triage_decision

    conn = init_db(single_db)
    try:
        decision = get_triage_decision(conn, "move-triage")
        assert decision is not None
        assert decision.action == "TO_ARCHIVE"
        assert decision.source == "user"
        assert decision.reason == "moved to TO_ARCHIVE"
        # mail_records.status was NOT updated.
        cur = conn.execute(
            "SELECT status FROM mail_records WHERE message_id = ?",
            ("move-triage",),
        )
        assert cur.fetchone()[0] == "to_read"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# POST /run-triage tests
# ---------------------------------------------------------------------------
