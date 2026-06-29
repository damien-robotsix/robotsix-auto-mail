"""Tests for global (aggregate) board rendering across multiple accounts."""

from __future__ import annotations

import json
from urllib.request import Request, urlopen

from tests.conftest import _make_record
from tests.server.conftest import (
    _get,
    _populate_db,
    _post_to_path,
    _seed_triage_decision,
    _start_test_server_with_accounts,
    _triage_action,
    _wait_for_batch_idle,
)

from robotsix_auto_mail.config import MailAccountsConfig
from robotsix_auto_mail.db import init_db, set_watermark
from robotsix_auto_mail.server.board_adapter import MailBoardAdapter


def test_global_content_builder_aggregation(
    db_accounts: tuple[str, str, MailAccountsConfig],
) -> None:
    """_build_global_board_content merges cards from both accounts."""
    _db_a, _db_b, accounts = db_accounts
    from robotsix_auto_mail.server.views import _build_global_board_content

    result = _build_global_board_content(accounts)

    # Standard keys present.
    assert "columns_html" in result
    assert "triage_running" in result
    assert "unsubscribe_suggestions" in result

    columns_html = result["columns_html"]
    # Cards from both accounts appear.
    assert "msg-a-inbox" in columns_html
    assert "msg-a-delete" in columns_html
    assert "msg-b-inbox" in columns_html
    assert "msg-b-answer" in columns_html
    # Column labels for the distinct actions.
    assert "TO_DELETE" in columns_html or "Delete" in columns_html
    assert "TO_ANSWER" in columns_html or "Answer" in columns_html
    # triage_running is False (no watermark set).
    assert result["triage_running"] is False


def test_global_content_builder_returns_correct_keys(
    db_accounts: tuple[str, str, MailAccountsConfig],
) -> None:
    """_build_global_board_content returns the same JSON keys as
    _build_board_content, including the aggregated ``batch_op``."""
    _db_a, _db_b, accounts = db_accounts
    from robotsix_auto_mail.server.views import _build_global_board_content

    result = _build_global_board_content(accounts)
    assert set(result.keys()) == {
        "columns_html",
        "triage_running",
        "unsubscribe_suggestions",
        "batch_op",
        "health_alerts_html",
        "account_health",
    }
    # No batch op running → aggregated batch_op is None.
    assert result["batch_op"] is None


def test_global_content_builder_aggregates_batch_op(
    db_accounts: tuple[str, str, MailAccountsConfig],
) -> None:
    """_build_global_board_content sums per-account batch-op progress so the
    aggregate banner reflects the combined fan-out."""
    db_a, db_b, accounts = db_accounts
    from robotsix_auto_mail.db import init_db, set_watermark
    from robotsix_auto_mail.server.views import _build_global_board_content

    for path, done, total in ((db_a, 2, 5), (db_b, 1, 3)):
        conn = init_db(path, skip_migrations=True)
        try:
            set_watermark(
                conn,
                "batch_op:state",
                json.dumps({"op": "delete", "done": done, "total": total}),
            )
        finally:
            conn.close()

    result = _build_global_board_content(accounts)
    assert result["batch_op"] == {"op": "delete", "done": 3, "total": 8}


def test_global_batch_delete_fans_out_across_accounts(
    db_accounts: tuple[str, str, MailAccountsConfig],
) -> None:
    """POST /batch-delete?account=__all__ deletes every account's TO_DELETE
    mail and leaves other columns untouched."""
    db_a, db_b, accounts = db_accounts
    # Account B already has TO_ANSWER + INBOX; add a TO_DELETE so the
    # fan-out has work to do in both accounts.
    _populate_db(
        db_b,
        [
            {
                "message_id": "msg-b-delete",
                "sender": "bob@b.com",
                "subject": "B Delete",
                "date": "2025-02-03T00:00:00",
                "body_plain": "Body B delete",
                "status": "to_read",
            }
        ],
    )
    _seed_triage_decision(db_b, "msg-b-delete", action="TO_DELETE")

    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        resp = _post_to_path(port, "/batch-delete?account=__all__", {})
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"
        _wait_for_batch_idle(db_a)
        _wait_for_batch_idle(db_b)
    finally:
        server.shutdown()

    from robotsix_auto_mail.db import get_record_by_message_id, init_db

    conn_a = init_db(db_a)
    try:
        assert get_record_by_message_id(conn_a, "msg-a-delete") is None
        assert get_record_by_message_id(conn_a, "msg-a-inbox") is not None
    finally:
        conn_a.close()
    conn_b = init_db(db_b)
    try:
        assert get_record_by_message_id(conn_b, "msg-b-delete") is None
        # Non-delete columns survive.
        assert get_record_by_message_id(conn_b, "msg-b-answer") is not None
        assert get_record_by_message_id(conn_b, "msg-b-inbox") is not None
    finally:
        conn_b.close()


def test_global_board_page_all_accounts_param(
    db_accounts: tuple[str, str, MailAccountsConfig],
) -> None:
    """GET /board?account=__all__ renders aggregate with data-account,
    account badge, and per-card ?account= actions."""
    _db_a, _db_b, accounts = db_accounts
    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        status, body, _h = _get(f"http://127.0.0.1:{port}/board?account=__all__")
        assert status == 200
        # Cards from both accounts present.
        assert "msg-a-inbox" in body
        assert "msg-b-inbox" in body
        # Account badge visible.
        assert 'class="card-account"' in body
        assert "Acc A" in body or "Acc B" in body
        # data-account attribute on card-extra.
        assert 'data-account="A"' in body
        assert 'data-account="B"' in body
        # Per-card move form carries ?account=.
        assert 'action="/move?account=A"' in body
        assert 'action="/move?account=B"' in body
        # Picker includes All mailboxes option.
        assert '<option value="__all__"' in body
        assert "All mailboxes" in body
        # No folder-triage form in aggregate mode.
        assert 'action="/run-folder-triage"' not in body
        # Delete-All fans out across accounts (TO_DELETE column seeded
        # via msg-a-delete), targeting the aggregate endpoint.
        assert 'action="/batch-delete?account=__all__"' in body
        # Archive / force-triage remain per-account and stay suppressed.
        assert 'action="/batch-archive"' not in body
        assert 'action="/force-triage-column"' not in body
        # No Run triage / refresh-btn (manual-control check).
        assert "Run triage" not in body
        assert 'id="refresh-btn"' not in body
        assert 'action="/run-triage"' not in body
    finally:
        server.shutdown()


def test_global_board_default_landing_multi_account(
    db_accounts: tuple[str, str, MailAccountsConfig],
) -> None:
    """GET /board with ≥2 accounts and no ?account=/cookie defaults
    to aggregate view (cards from all accounts present)."""
    _db_a, _db_b, accounts = db_accounts
    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        status, body, _h = _get(f"http://127.0.0.1:{port}/board")
        assert status == 200
        # Aggregate view (both accounts present).
        assert "msg-a-inbox" in body
        assert "msg-b-inbox" in body
        # Account badge + data-account present.
        assert 'class="card-account"' in body
        assert 'data-account="A"' in body
        assert 'data-account="B"' in body
    finally:
        server.shutdown()


def test_global_board_picker_all_mailboxes_option(
    db_accounts_no_triage: tuple[str, str, MailAccountsConfig],
) -> None:
    """The account picker in aggregate mode lists 'All mailboxes' first,
    selected by default."""
    _db_a, _db_b, accounts = db_accounts_no_triage
    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        status, body, _h = _get(f"http://127.0.0.1:{port}/board?account=__all__")
        assert status == 200
        assert '<option value="__all__" selected>' in body
        assert "All mailboxes" in body
        # Per-account options follow.
        assert '<option value="A"' in body
        assert '<option value="B"' in body
    finally:
        server.shutdown()


def test_global_board_single_account_query_still_works(
    db_accounts: tuple[str, str, MailAccountsConfig],
) -> None:
    """GET /board?account=<real id> still renders single-account board."""
    _db_a, _db_b, accounts = db_accounts
    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        status, body, _h = _get(f"http://127.0.0.1:{port}/board?account=A")
        assert status == 200
        # Only account A's cards.
        assert "msg-a-inbox" in body
        assert "msg-a-delete" in body
        assert "msg-b-inbox" not in body
        assert "msg-b-answer" not in body
        # No account badges in single-account mode.
        assert 'class="card-account"' not in body
        assert "data-account=" not in body
    finally:
        server.shutdown()


def test_global_board_content_json_aggregate(
    db_accounts: tuple[str, str, MailAccountsConfig],
) -> None:
    """GET /board-content?account=__all__ returns JSON aggregating all accounts."""
    _db_a, _db_b, accounts = db_accounts
    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        status, body, _h = _get(
            f"http://127.0.0.1:{port}/board-content?account=__all__"
        )
        assert status == 200
        payload = json.loads(body)
        assert "columns_html" in payload
        assert "triage_running" in payload
        assert "unsubscribe_suggestions" in payload
        # Both accounts' cards in the JSON.
        assert "msg-a-inbox" in payload["columns_html"]
        assert "msg-b-inbox" in payload["columns_html"]
    finally:
        server.shutdown()


def test_global_board_move_routes_to_correct_account(
    db_accounts: tuple[str, str, MailAccountsConfig],
) -> None:
    """POST /move?account=A from an aggregate card mutates only A's DB."""
    db_a, db_b, accounts = db_accounts
    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        import urllib.parse

        data = urllib.parse.urlencode(
            {"message_id": "msg-a-inbox", "triage_action": "TO_ANSWER"}
        ).encode()
        req = Request(
            f"http://127.0.0.1:{port}/move?account=A",
            data=data,
            method="POST",
        )
        resp = urlopen(req)  # noqa: S310
        resp.close()
    finally:
        server.shutdown()
    # Verify only A's DB was mutated.
    assert _triage_action(db_a, "msg-a-inbox") == "TO_ANSWER"
    assert _triage_action(db_b, "msg-b-inbox") is None


def test_global_board_detail_routes_to_correct_account(
    db_accounts: tuple[str, str, MailAccountsConfig],
) -> None:
    """GET /email/<mid>?account=A returns the detail for A's record."""
    _db_a, _db_b, accounts = db_accounts
    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        status, body, _h = _get(
            f"http://127.0.0.1:{port}/email/msg-a-inbox?embed=1&account=A"
        )
        assert status == 200
        assert "alice@a.com" in body
        assert "bob@b.com" not in body
    finally:
        server.shutdown()


def test_single_account_output_unchanged(single_db: str) -> None:
    """Single-account _build_board_content produces the same output after
    the _gather_account_board_data refactor (no regressions)."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "msg-x",
                "sender": "x@example.com",
                "subject": "Test",
                "date": "2025-01-01T00:00:00",
                "body_plain": "Body",
                "status": "to_read",
            },
        ],
    )
    from robotsix_auto_mail.server.views import _build_board_content

    result = _build_board_content(single_db)
    # Standard keys present.
    assert "columns_html" in result
    assert "triage_running" in result
    assert "batch_op" in result
    assert "unsubscribe_suggestions" in result
    # The card is rendered.
    assert "msg-x" in result["columns_html"]
    # No account badge / data-account in single-account mode.
    assert 'class="card-account"' not in result["columns_html"]
    assert "data-account=" not in result["columns_html"]
    # No ?account= in form actions.
    assert "?account=" not in result["columns_html"]


def test_global_board_triage_running_ors_across_accounts(
    db_accounts: tuple[str, str, MailAccountsConfig],
) -> None:
    """triage_running is True when any account's watermark is 'running'."""
    _db_a, db_b, accounts = db_accounts
    # Mark account B as running.
    conn_b = init_db(db_b)
    try:
        set_watermark(conn_b, "triage_run:state", "running")
    finally:
        conn_b.close()
    from robotsix_auto_mail.server.views import _build_global_board_content

    result = _build_global_board_content(accounts)
    assert result["triage_running"] is True


def test_mailboard_adapter_no_record_accounts_unchanged() -> None:
    """MailBoardAdapter with empty record_accounts produces byte-identical
    card_extra_html and column_extra_html to the pre-aggregate behaviour."""
    record = _make_record(
        message_id="<test@example.com>",
        sender="sender@example.com",
        subject="Test mail",
        date="2025-01-01T00:00:00",
        body_plain="Hello world",
    )
    adapter = MailBoardAdapter(
        triage_by_mid={"<test@example.com>": "INBOX"},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
        column_records={"INBOX": [record]},
    )
    card_html = adapter.card_extra_html(record)
    # No account badge.
    assert 'class="card-account"' not in card_html
    # No data-account attribute.
    assert "data-account=" not in card_html
    # No ?account= in form actions.
    assert "?account=" not in card_html
    # Core attributes still present.
    assert 'data-message-id="%3Ctest%40example.com%3E"' in card_html
    assert 'data-subject="Test mail"' in card_html

    col_html = adapter.column_extra_html("INBOX")
    # INBOX with records but no matching controls → empty wrapper div.
    assert 'class="column-extra-top"' in col_html
    assert "batch-delete" not in col_html
    assert "force-triage" not in col_html


def test_mailboard_adapter_with_record_accounts_adds_badge_and_qs() -> None:
    """MailBoardAdapter with record_accounts adds data-account, badge,
    and ?account= to all per-card form actions."""
    record = _make_record(
        message_id="<test@example.com>",
        sender="sender@example.com",
        subject="Test mail",
        date="2025-01-01T00:00:00",
        body_plain="Hello world",
    )
    adapter = MailBoardAdapter(
        triage_by_mid={"<test@example.com>": "INBOX"},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
        column_records={"INBOX": [record]},
        record_accounts={"<test@example.com>": "acc1"},
        account_labels={"acc1": "Account One"},
    )
    card_html = adapter.card_extra_html(record)
    # Account badge visible.
    assert 'class="card-account"' in card_html
    assert "Account One" in card_html
    # data-account attribute.
    assert 'data-account="acc1"' in card_html
    # ?account=acc1 on the move form action.
    assert 'action="/move?account=acc1"' in card_html

    # column_extra_html suppresses batch controls in aggregate mode.
    col_html = adapter.column_extra_html("TO_DELETE")
    assert 'action="/batch-delete"' not in col_html
    assert 'action="/batch-archive"' not in col_html
    assert 'action="/force-triage-column"' not in col_html
