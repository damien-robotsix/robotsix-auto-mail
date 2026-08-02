"""Tests for the CLI serve subcommand background ingest loop."""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from tests.cli.conftest import _accounts


class _StopLoopError(Exception):
    """Raised by a mock ``time.sleep`` to break the infinite ingest loop."""


# ---------------------------------------------------------------------------
# _ingest_loop
# ---------------------------------------------------------------------------


def test_ingest_loop_calls_ingest_cycle_for_account_with_password(
    cfg: MailConfig,
) -> None:
    """For each account with a configured password, ``_ingest_cycle`` is
    called with ``dry_run=False``."""
    from robotsix_auto_mail.cli.commands_serve import _ingest_loop

    mock_ingest_cycle = mock.Mock()
    test_accounts = _accounts(cfg)
    mock_load_accounts = mock.Mock(return_value=test_accounts)

    def _sleep_side_effect(seconds: float) -> None:
        raise _StopLoopError

    with (
        mock.patch("robotsix_auto_mail.config.load_accounts", mock_load_accounts),
        mock.patch(
            "robotsix_auto_mail.cli.commands_ingest._ingest_cycle",
            mock_ingest_cycle,
        ),
        mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[],
        ),
        mock.patch("robotsix_auto_mail.cli.commands_serve.time.sleep") as mock_sleep,
    ):
        mock_sleep.side_effect = _sleep_side_effect
        with pytest.raises(_StopLoopError):
            _ingest_loop(test_accounts)

    mock_ingest_cycle.assert_called_once_with(cfg, dry_run=False)


def test_ingest_loop_skips_account_without_password() -> None:
    """An account with no password is skipped in the ingest loop."""
    from robotsix_auto_mail.cli.commands_serve import _ingest_loop

    mock_ingest_cycle = mock.Mock()

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
        mock.patch(
            "robotsix_auto_mail.cli.commands_ingest._ingest_cycle",
            mock_ingest_cycle,
        ),
        mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[],
        ),
        mock.patch("robotsix_auto_mail.cli.commands_serve.time.sleep") as mock_sleep,
    ):
        mock_sleep.side_effect = _sleep_side_effect
        with pytest.raises(_StopLoopError):
            _ingest_loop(accounts)

    mock_ingest_cycle.assert_not_called()


def test_ingest_loop_survives_ingest_cycle_exception(
    cfg: MailConfig,
) -> None:
    """When ``_ingest_cycle`` raises an exception, the loop survives and
    continues to the next iteration."""
    from robotsix_auto_mail.cli.commands_serve import _ingest_loop

    mock_ingest_cycle = mock.Mock(side_effect=OSError("network down"))
    test_accounts = _accounts(cfg)
    mock_load_accounts = mock.Mock(return_value=test_accounts)

    def _sleep_side_effect(seconds: float) -> None:
        raise _StopLoopError

    with (
        mock.patch("robotsix_auto_mail.config.load_accounts", mock_load_accounts),
        mock.patch(
            "robotsix_auto_mail.cli.commands_ingest._ingest_cycle",
            mock_ingest_cycle,
        ),
        mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[],
        ),
        mock.patch("robotsix_auto_mail.cli.commands_serve.time.sleep") as mock_sleep,
    ):
        mock_sleep.side_effect = _sleep_side_effect
        with pytest.raises(_StopLoopError):
            _ingest_loop(test_accounts)

    # The loop must not crash — reaching StopLoop proves it survived.
    mock_ingest_cycle.assert_called_once_with(cfg, dry_run=False)


def test_ingest_loop_respects_ingest_interval() -> None:
    """The sleep interval is derived from the minimum configured
    ``ingest_interval_minutes`` across accounts, converted to seconds."""
    from robotsix_auto_mail.cli.commands_serve import _ingest_loop

    mock_ingest_cycle = mock.Mock()

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
        default_account_id="a",
    )

    mock_load_accounts = mock.Mock(return_value=accounts)

    def _sleep_side_effect(seconds: float) -> None:
        assert seconds == 3 * 60  # 3 minutes in seconds
        raise _StopLoopError

    with (
        mock.patch("robotsix_auto_mail.config.load_accounts", mock_load_accounts),
        mock.patch(
            "robotsix_auto_mail.cli.commands_ingest._ingest_cycle",
            mock_ingest_cycle,
        ),
        mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[],
        ),
        mock.patch("robotsix_auto_mail.cli.commands_serve.time.sleep") as mock_sleep,
    ):
        mock_sleep.side_effect = _sleep_side_effect
        with pytest.raises(_StopLoopError):
            _ingest_loop(accounts)


def test_ingest_loop_survives_load_accounts_failure(
    cfg: MailConfig,
) -> None:
    """When ``load_accounts`` raises an exception, the loop keeps using the
    last-known snapshot and continues."""
    from robotsix_auto_mail.cli.commands_serve import _ingest_loop

    mock_ingest_cycle = mock.Mock()
    test_accounts = _accounts(cfg)
    # First call returns valid accounts; the per-cycle reload fails.
    mock_load_accounts = mock.Mock(
        side_effect=[test_accounts, OSError("config unreadable")]
    )

    def _sleep_side_effect(seconds: float) -> None:
        raise _StopLoopError

    with (
        mock.patch("robotsix_auto_mail.config.load_accounts", mock_load_accounts),
        mock.patch(
            "robotsix_auto_mail.cli.commands_ingest._ingest_cycle",
            mock_ingest_cycle,
        ),
        mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[],
        ),
        mock.patch("robotsix_auto_mail.cli.commands_serve.time.sleep") as mock_sleep,
    ):
        mock_sleep.side_effect = _sleep_side_effect
        with pytest.raises(_StopLoopError):
            _ingest_loop(test_accounts)

    # _ingest_cycle should still be called (using the initial snapshot).
    mock_ingest_cycle.assert_called_once_with(cfg, dry_run=False)


def test_ingest_loop_handles_empty_accounts() -> None:
    """When no accounts exist, the loop uses the default fallback interval
    and does not call ``_ingest_cycle``."""
    from robotsix_auto_mail.cli.commands_serve import _ingest_loop

    mock_ingest_cycle = mock.Mock()

    accounts = MailAccountsConfig(
        accounts=(),
        default_account_id="",
    )

    mock_load_accounts = mock.Mock(return_value=accounts)

    def _sleep_side_effect(seconds: float) -> None:
        assert seconds == 15 * 60  # default fallback: 15 minutes
        raise _StopLoopError

    with (
        mock.patch("robotsix_auto_mail.config.load_accounts", mock_load_accounts),
        mock.patch(
            "robotsix_auto_mail.cli.commands_ingest._ingest_cycle",
            mock_ingest_cycle,
        ),
        mock.patch(
            "robotsix_auto_mail.settings.store.discover_accounts_from_settings_stores",
            return_value=[],
        ),
        mock.patch("robotsix_auto_mail.cli.commands_serve.time.sleep") as mock_sleep,
    ):
        mock_sleep.side_effect = _sleep_side_effect
        with pytest.raises(_StopLoopError):
            _ingest_loop(accounts)

    mock_ingest_cycle.assert_not_called()
