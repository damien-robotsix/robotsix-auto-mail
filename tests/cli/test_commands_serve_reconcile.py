"""Tests for the CLI serve subcommand background reconcile loop."""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from tests.cli.conftest import _accounts


class _StopLoopError(Exception):
    """Raised by a mock ``time.sleep`` to break the infinite reconcile loop."""


# ---------------------------------------------------------------------------
# _reconcile_loop
# ---------------------------------------------------------------------------


def test_reconcile_loop_spawns_thread_when_watermark_free(
    cfg: MailConfig,
) -> None:
    """When the reconcile:state watermark is *not* ``running``, a background
    reconcile thread is spawned."""
    from robotsix_auto_mail.cli.commands_serve import _reconcile_loop

    mock_conn = mock.MagicMock()
    mock_init_db = mock.Mock(return_value=mock_conn)
    mock_get_watermark = mock.Mock(return_value=None)  # not "running"
    mock_set_watermark = mock.Mock()
    mock_run_reconcile = mock.Mock()

    test_accounts = _accounts(cfg)
    mock_load_accounts = mock.Mock(return_value=test_accounts)

    def _sleep_side_effect(seconds: float) -> None:
        raise _StopLoopError

    with (
        mock.patch("robotsix_auto_mail.config.load_accounts", mock_load_accounts),
        mock.patch("robotsix_auto_mail.db.init_db", mock_init_db),
        mock.patch("robotsix_auto_mail.db.get_watermark", mock_get_watermark),
        mock.patch("robotsix_auto_mail.db.set_watermark", mock_set_watermark),
        mock.patch(
            "robotsix_auto_mail.server.adapters._run_reconcile_background",
            mock_run_reconcile,
        ),
        mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[],
        ),
        mock.patch("robotsix_auto_mail.cli.commands_serve.time.sleep") as mock_sleep,
    ):
        mock_sleep.side_effect = _sleep_side_effect
        with pytest.raises(_StopLoopError):
            _reconcile_loop(test_accounts)

    mock_init_db.assert_called_once()
    mock_set_watermark.assert_called_once_with(mock_conn, "reconcile:state", "running")
    mock_run_reconcile.assert_called_once_with(cfg.db_path, cfg)
    mock_conn.close.assert_called_once()


def test_reconcile_loop_skips_when_already_running(
    cfg: MailConfig,
) -> None:
    """When the reconcile:state watermark is already ``running``, no new
    thread is spawned."""
    from robotsix_auto_mail.cli.commands_serve import _reconcile_loop

    mock_conn = mock.MagicMock()
    mock_init_db = mock.Mock(return_value=mock_conn)
    mock_get_watermark = mock.Mock(return_value="running")
    mock_set_watermark = mock.Mock()
    mock_run_reconcile = mock.Mock()

    test_accounts = _accounts(cfg)
    mock_load_accounts = mock.Mock(return_value=test_accounts)

    def _sleep_side_effect(seconds: float) -> None:
        raise _StopLoopError

    with (
        mock.patch("robotsix_auto_mail.config.load_accounts", mock_load_accounts),
        mock.patch("robotsix_auto_mail.db.init_db", mock_init_db),
        mock.patch("robotsix_auto_mail.db.get_watermark", mock_get_watermark),
        mock.patch("robotsix_auto_mail.db.set_watermark", mock_set_watermark),
        mock.patch(
            "robotsix_auto_mail.server.adapters._run_reconcile_background",
            mock_run_reconcile,
        ),
        mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[],
        ),
        mock.patch("robotsix_auto_mail.cli.commands_serve.time.sleep") as mock_sleep,
    ):
        mock_sleep.side_effect = _sleep_side_effect
        with pytest.raises(_StopLoopError):
            _reconcile_loop(test_accounts)

    mock_set_watermark.assert_not_called()
    mock_run_reconcile.assert_not_called()


def test_reconcile_loop_survives_db_init_error(
    cfg: MailConfig,
) -> None:
    """When ``init_db`` raises an exception, the loop survives and continues
    to the next iteration."""
    from robotsix_auto_mail.cli.commands_serve import _reconcile_loop

    mock_init_db = mock.Mock(side_effect=OSError("disk full"))
    mock_get_watermark = mock.Mock()
    mock_run_reconcile = mock.Mock()

    test_accounts = _accounts(cfg)
    mock_load_accounts = mock.Mock(return_value=test_accounts)

    def _sleep_side_effect(seconds: float) -> None:
        raise _StopLoopError

    with (
        mock.patch("robotsix_auto_mail.config.load_accounts", mock_load_accounts),
        mock.patch("robotsix_auto_mail.db.init_db", mock_init_db),
        mock.patch("robotsix_auto_mail.db.get_watermark", mock_get_watermark),
        mock.patch(
            "robotsix_auto_mail.server.adapters._run_reconcile_background",
            mock_run_reconcile,
        ),
        mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[],
        ),
        mock.patch("robotsix_auto_mail.cli.commands_serve.time.sleep") as mock_sleep,
    ):
        mock_sleep.side_effect = _sleep_side_effect
        with pytest.raises(_StopLoopError):
            _reconcile_loop(test_accounts)

    # The loop must not crash — reaching StopLoop proves it survived.
    mock_init_db.assert_called()
    mock_get_watermark.assert_not_called()
    mock_run_reconcile.assert_not_called()


def test_reconcile_loop_survives_watermark_error(
    cfg: MailConfig,
) -> None:
    """When ``get_watermark`` raises an exception, the loop survives."""
    from robotsix_auto_mail.cli.commands_serve import _reconcile_loop

    mock_conn = mock.MagicMock()
    mock_init_db = mock.Mock(return_value=mock_conn)
    mock_get_watermark = mock.Mock(side_effect=OSError("read error"))
    mock_run_reconcile = mock.Mock()

    test_accounts = _accounts(cfg)
    mock_load_accounts = mock.Mock(return_value=test_accounts)

    def _sleep_side_effect(seconds: float) -> None:
        raise _StopLoopError

    with (
        mock.patch("robotsix_auto_mail.config.load_accounts", mock_load_accounts),
        mock.patch("robotsix_auto_mail.db.init_db", mock_init_db),
        mock.patch("robotsix_auto_mail.db.get_watermark", mock_get_watermark),
        mock.patch(
            "robotsix_auto_mail.server.adapters._run_reconcile_background",
            mock_run_reconcile,
        ),
        mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[],
        ),
        mock.patch("robotsix_auto_mail.cli.commands_serve.time.sleep") as mock_sleep,
    ):
        mock_sleep.side_effect = _sleep_side_effect
        with pytest.raises(_StopLoopError):
            _reconcile_loop(test_accounts)

    mock_init_db.assert_called_once()
    mock_get_watermark.assert_called_once()
    mock_run_reconcile.assert_not_called()
    mock_conn.close.assert_called_once()


def test_reconcile_loop_respects_ingest_interval(
    cfg: MailConfig,
) -> None:
    """The sleep interval is derived from the minimum configured
    ``ingest_interval_minutes`` across accounts, converted to seconds."""
    from robotsix_auto_mail.cli.commands_serve import _reconcile_loop

    mock_conn = mock.MagicMock()
    mock_init_db = mock.Mock(return_value=mock_conn)
    mock_get_watermark = mock.Mock(return_value="running")

    # Two accounts with different intervals; minimum should be 3.
    accounts = MailAccountsConfig(
        accounts=(
            MailAccount(
                account_id="a",
                config=MailConfig(
                    imap_host="h1",
                    smtp_host="h1",
                    username="u1",
                    password="p1",
                    db_path=":memory:?a",
                    ingest_interval_minutes=5,
                ),
                label=None,
            ),
            MailAccount(
                account_id="b",
                config=MailConfig(
                    imap_host="h2",
                    smtp_host="h2",
                    username="u2",
                    password="p2",
                    db_path=":memory:?b",
                    ingest_interval_minutes=3,
                ),
                label=None,
            ),
        ),
    )

    mock_load_accounts = mock.Mock(return_value=accounts)

    def _sleep_side_effect(seconds: float) -> None:
        assert seconds == 3 * 60  # 3 minutes in seconds
        raise _StopLoopError

    with (
        mock.patch("robotsix_auto_mail.config.load_accounts", mock_load_accounts),
        mock.patch("robotsix_auto_mail.db.init_db", mock_init_db),
        mock.patch("robotsix_auto_mail.db.get_watermark", mock_get_watermark),
        mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[],
        ),
        mock.patch("robotsix_auto_mail.cli.commands_serve.time.sleep") as mock_sleep,
    ):
        mock_sleep.side_effect = _sleep_side_effect
        with pytest.raises(_StopLoopError):
            _reconcile_loop(accounts)


def test_reconcile_loop_skips_account_without_password() -> None:
    """An account with no password is skipped in the reconcile loop."""
    from robotsix_auto_mail.cli.commands_serve import _reconcile_loop

    mock_conn = mock.MagicMock()
    mock_init_db = mock.Mock(return_value=mock_conn)
    mock_get_watermark = mock.Mock(return_value=None)
    mock_run_reconcile = mock.Mock()

    cfg_no_pw = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="",
        db_path=":memory:?nopw",
    )
    accounts = _accounts(cfg_no_pw, account_id="nopw")

    mock_load_accounts = mock.Mock(return_value=accounts)

    def _sleep_side_effect(seconds: float) -> None:
        raise _StopLoopError

    with (
        mock.patch("robotsix_auto_mail.config.load_accounts", mock_load_accounts),
        mock.patch("robotsix_auto_mail.db.init_db", mock_init_db),
        mock.patch("robotsix_auto_mail.db.get_watermark", mock_get_watermark),
        mock.patch(
            "robotsix_auto_mail.server.adapters._run_reconcile_background",
            mock_run_reconcile,
        ),
        mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[],
        ),
        mock.patch("robotsix_auto_mail.cli.commands_serve.time.sleep") as mock_sleep,
    ):
        mock_sleep.side_effect = _sleep_side_effect
        with pytest.raises(_StopLoopError):
            _reconcile_loop(accounts)

    # The password-less account should be skipped entirely — no DB open,
    # no reconcile spawn.
    mock_init_db.assert_not_called()
    mock_get_watermark.assert_not_called()
    mock_run_reconcile.assert_not_called()
