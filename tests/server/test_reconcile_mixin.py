"""Unit tests for _ReconcileMixin._handle_reconcile."""

from __future__ import annotations

from unittest import mock

from pydantic import SecretStr

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.server._reconcile_mixin import _ReconcileMixin


class _FakeReconcileHandler(_ReconcileMixin):
    """Concrete handler wiring protocol stubs for direct mixin testing."""

    def __init__(
        self,
        *,
        db_path: str = "/tmp/test.db",  # noqa: S108
        mail_config: MailConfig | None = None,
        aggregate: bool = False,
        accounts: MailAccountsConfig | None = None,
    ) -> None:
        self.db_path = db_path
        self.mail_config = mail_config
        self._aggregate = aggregate
        self.accounts = accounts
        self._launch_background_worker = mock.MagicMock()
        self._redirect = mock.MagicMock()


def _make_mail_config(db_path: str = "/tmp/test.db") -> MailConfig:  # noqa: S108
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password=SecretStr("secret"),
        db_path=db_path,
    )


def _make_accounts(count: int, *, base_db_path: str = "/tmp/acct") -> MailAccountsConfig:  # noqa: S108
    accounts = [
        MailAccount(
            account_id=f"acct-{i}",
            config=_make_mail_config(db_path=f"{base_db_path}-{i}.db"),
        )
        for i in range(count)
    ]
    return MailAccountsConfig(
        accounts=accounts,
        default_account_id=accounts[0].account_id if accounts else "",
    )


# ---------------------------------------------------------------------------
# Idempotency guard
# ---------------------------------------------------------------------------


def test_handle_reconcile_idempotent_returns_early() -> None:
    """When _launch_background_worker returns False, return without spawning
    threads or redirecting."""
    handler = _FakeReconcileHandler(aggregate=False)
    handler._launch_background_worker.return_value = False

    with mock.patch(
        "robotsix_auto_mail.server._reconcile_mixin.threading.Thread"
    ) as mock_thread:
        handler._handle_reconcile()

    mock_thread.assert_not_called()
    handler._redirect.assert_not_called()


# ---------------------------------------------------------------------------
# Aggregate mode — one thread per account
# ---------------------------------------------------------------------------


def test_handle_reconcile_aggregate_spawns_per_account_threads() -> None:
    """With _aggregate=True and 3 accounts, spawns exactly 3 threads with
    per-account args and redirects to /board."""
    accounts = _make_accounts(3)
    handler = _FakeReconcileHandler(aggregate=True, accounts=accounts)
    handler._launch_background_worker.return_value = True

    with mock.patch(
        "robotsix_auto_mail.server._reconcile_mixin.threading.Thread"
    ) as mock_thread:
        handler._handle_reconcile()

    assert mock_thread.call_count == 3
    # Each call: Thread(target=_run_reconcile_background,
    #                  args=(acct.config.db_path, acct.config), daemon=True)
    for i, call_args in enumerate(mock_thread.call_args_list):
        kwargs = call_args.kwargs
        assert kwargs["target"].__name__ == "_run_reconcile_background"
        assert kwargs["daemon"] is True
        expected_db = f"/tmp/acct-{i}.db"  # noqa: S108
        assert kwargs["args"][0] == expected_db
        assert kwargs["args"][1] is accounts.accounts[i].config

    handler._redirect.assert_called_once_with("/board", code=302)


# ---------------------------------------------------------------------------
# Non-aggregate mode — single thread
# ---------------------------------------------------------------------------


def test_handle_reconcile_non_aggregate_single_thread() -> None:
    """Without _aggregate, spawns a single thread with db_path + mail_config
    and redirects to /board."""
    config = _make_mail_config(db_path="/data/mail.db")
    handler = _FakeReconcileHandler(
        db_path="/data/mail.db",
        mail_config=config,
        aggregate=False,
    )
    handler._launch_background_worker.return_value = True

    with mock.patch(
        "robotsix_auto_mail.server._reconcile_mixin.threading.Thread"
    ) as mock_thread:
        handler._handle_reconcile()

    mock_thread.assert_called_once()
    kwargs = mock_thread.call_args.kwargs
    assert kwargs["target"].__name__ == "_run_reconcile_background"
    assert kwargs["daemon"] is True
    assert kwargs["args"] == ("/data/mail.db", config)

    handler._redirect.assert_called_once_with("/board", code=302)
