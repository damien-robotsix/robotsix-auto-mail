"""Tests for the CLI serve subcommand and background reconcile loop."""

from __future__ import annotations

import errno
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig


def _accounts(cfg: MailConfig, account_id: str = "default") -> MailAccountsConfig:
    """Wrap a single ``MailConfig`` in a one-element accounts container."""
    return MailAccountsConfig(
        accounts=(MailAccount(account_id=account_id, config=cfg, label=None),),
        default_account_id=account_id,
    )


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

    def _sleep_side_effect(seconds: float) -> None:
        raise _StopLoopError

    with (
        mock.patch("robotsix_auto_mail.db.init_db", mock_init_db),
        mock.patch("robotsix_auto_mail.db.get_watermark", mock_get_watermark),
        mock.patch("robotsix_auto_mail.db.set_watermark", mock_set_watermark),
        mock.patch(
            "robotsix_auto_mail.server.adapters._run_reconcile_background",
            mock_run_reconcile,
        ),
        mock.patch("robotsix_auto_mail.cli.commands_serve.time.sleep") as mock_sleep,
    ):
        mock_sleep.side_effect = _sleep_side_effect
        with pytest.raises(_StopLoopError):
            _reconcile_loop(_accounts(cfg))

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

    def _sleep_side_effect(seconds: float) -> None:
        raise _StopLoopError

    with (
        mock.patch("robotsix_auto_mail.db.init_db", mock_init_db),
        mock.patch("robotsix_auto_mail.db.get_watermark", mock_get_watermark),
        mock.patch("robotsix_auto_mail.db.set_watermark", mock_set_watermark),
        mock.patch(
            "robotsix_auto_mail.server.adapters._run_reconcile_background",
            mock_run_reconcile,
        ),
        mock.patch("robotsix_auto_mail.cli.commands_serve.time.sleep") as mock_sleep,
    ):
        mock_sleep.side_effect = _sleep_side_effect
        with pytest.raises(_StopLoopError):
            _reconcile_loop(_accounts(cfg))

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

    def _sleep_side_effect(seconds: float) -> None:
        raise _StopLoopError

    with (
        mock.patch("robotsix_auto_mail.db.init_db", mock_init_db),
        mock.patch("robotsix_auto_mail.db.get_watermark", mock_get_watermark),
        mock.patch(
            "robotsix_auto_mail.server.adapters._run_reconcile_background",
            mock_run_reconcile,
        ),
        mock.patch("robotsix_auto_mail.cli.commands_serve.time.sleep") as mock_sleep,
    ):
        mock_sleep.side_effect = _sleep_side_effect
        with pytest.raises(_StopLoopError):
            _reconcile_loop(_accounts(cfg))

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

    def _sleep_side_effect(seconds: float) -> None:
        raise _StopLoopError

    with (
        mock.patch("robotsix_auto_mail.db.init_db", mock_init_db),
        mock.patch("robotsix_auto_mail.db.get_watermark", mock_get_watermark),
        mock.patch(
            "robotsix_auto_mail.server.adapters._run_reconcile_background",
            mock_run_reconcile,
        ),
        mock.patch("robotsix_auto_mail.cli.commands_serve.time.sleep") as mock_sleep,
    ):
        mock_sleep.side_effect = _sleep_side_effect
        with pytest.raises(_StopLoopError):
            _reconcile_loop(_accounts(cfg))

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
        default_account_id="a",
    )

    def _sleep_side_effect(seconds: float) -> None:
        assert seconds == 3 * 60  # 3 minutes in seconds
        raise _StopLoopError

    with (
        mock.patch("robotsix_auto_mail.db.init_db", mock_init_db),
        mock.patch("robotsix_auto_mail.db.get_watermark", mock_get_watermark),
        mock.patch("robotsix_auto_mail.cli.commands_serve.time.sleep") as mock_sleep,
    ):
        mock_sleep.side_effect = _sleep_side_effect
        with pytest.raises(_StopLoopError):
            _reconcile_loop(accounts)


# ---------------------------------------------------------------------------
# _cmd_serve
# ---------------------------------------------------------------------------


def test_cmd_serve_starts_http_server(
    cfg: MailConfig,
) -> None:
    """_cmd_serve wires up an HTTPServer on 0.0.0.0:<port> with the board
    handler class and calls serve_forever."""
    from robotsix_auto_mail.cli.commands_serve import _cmd_serve

    accounts = _accounts(cfg)
    mock_handler_class = mock.MagicMock()
    mock_server = mock.MagicMock()

    with (
        mock.patch(
            "robotsix_auto_mail.server.make_board_handler",
            return_value=mock_handler_class,
        ),
        mock.patch(
            "http.server.HTTPServer",
            return_value=mock_server,
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve._clear_stale_triage_state",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve._reconcile_loop",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve.threading.Thread",
        ),
    ):
        _cmd_serve(accounts, default_account_id="default", port=8099)

    mock_server.serve_forever.assert_called_once()


def test_cmd_serve_creates_component_responder_when_enabled(
    cfg: MailConfig,
) -> None:
    """When ``component_agent_enabled`` is True, a ComponentAgentResponder
    is created and passed to make_board_handler."""
    from robotsix_auto_mail.cli.commands_serve import _cmd_serve

    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        component_agent_enabled=True,
    )
    accounts = _accounts(cfg)
    mock_handler_class = mock.MagicMock()
    mock_server = mock.MagicMock()
    mock_responder = mock.MagicMock()

    with (
        mock.patch(
            "robotsix_auto_mail.server.make_board_handler",
            return_value=mock_handler_class,
        ) as mock_make_handler,
        mock.patch(
            "http.server.HTTPServer",
            return_value=mock_server,
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve._clear_stale_triage_state",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve._reconcile_loop",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve.threading.Thread",
        ),
        mock.patch(
            "robotsix_auto_mail.server._component_agent_responder.ComponentAgentResponder",
            return_value=mock_responder,
        ) as mock_responder_cls,
    ):
        _cmd_serve(accounts, default_account_id="default", port=8099)

    mock_responder_cls.assert_called_once_with(cfg)
    mock_make_handler.assert_called_once()
    _, kwargs = mock_make_handler.call_args
    assert kwargs["component_responder"] is mock_responder


def test_cmd_serve_no_component_responder_when_disabled(
    cfg: MailConfig,
) -> None:
    """When ``component_agent_enabled`` is False (the default), no
    ComponentAgentResponder is created and ``None`` is passed."""
    from robotsix_auto_mail.cli.commands_serve import _cmd_serve

    accounts = _accounts(cfg)  # component_agent_enabled=False by default
    mock_handler_class = mock.MagicMock()
    mock_server = mock.MagicMock()

    with (
        mock.patch(
            "robotsix_auto_mail.server.make_board_handler",
            return_value=mock_handler_class,
        ) as mock_make_handler,
        mock.patch(
            "http.server.HTTPServer",
            return_value=mock_server,
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve._clear_stale_triage_state",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve._reconcile_loop",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve.threading.Thread",
        ),
    ):
        _cmd_serve(accounts, default_account_id="default", port=8099)

    _, kwargs = mock_make_handler.call_args
    assert kwargs["component_responder"] is None


def test_cmd_serve_clears_stale_triage_state(
    cfg: MailConfig,
) -> None:
    """_cmd_serve calls _clear_stale_triage_state with the accounts config."""
    from robotsix_auto_mail.cli.commands_serve import _cmd_serve

    accounts = _accounts(cfg)
    mock_handler_class = mock.MagicMock()
    mock_server = mock.MagicMock()

    with (
        mock.patch(
            "robotsix_auto_mail.server.make_board_handler",
            return_value=mock_handler_class,
        ),
        mock.patch(
            "http.server.HTTPServer",
            return_value=mock_server,
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve._clear_stale_triage_state",
        ) as mock_clear,
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve._reconcile_loop",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve.threading.Thread",
        ),
    ):
        _cmd_serve(accounts, default_account_id="default", port=8099)

    mock_clear.assert_called_once_with(accounts)


def test_cmd_serve_starts_reconcile_background_thread(
    cfg: MailConfig,
) -> None:
    """_cmd_serve spawns _reconcile_loop in a daemon thread."""
    from robotsix_auto_mail.cli.commands_serve import _cmd_serve

    accounts = _accounts(cfg)
    mock_handler_class = mock.MagicMock()
    mock_server = mock.MagicMock()

    with (
        mock.patch(
            "robotsix_auto_mail.server.make_board_handler",
            return_value=mock_handler_class,
        ),
        mock.patch(
            "http.server.HTTPServer",
            return_value=mock_server,
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve._clear_stale_triage_state",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve._reconcile_loop",
        ) as mock_reconcile,
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve.threading.Thread",
        ) as mock_thread_cls,
    ):
        _cmd_serve(accounts, default_account_id="default", port=8099)

    mock_thread_cls.assert_called_once_with(
        target=mock_reconcile, args=(accounts,), daemon=True
    )
    mock_thread_cls.return_value.start.assert_called_once()


def test_cmd_serve_eaddrinuse_returns_1(
    cfg: MailConfig,
) -> None:
    """When the port is already in use (EADDRINUSE), _cmd_serve returns 1."""
    from robotsix_auto_mail.cli.commands_serve import _cmd_serve

    accounts = _accounts(cfg)
    mock_handler_class = mock.MagicMock()

    with (
        mock.patch(
            "robotsix_auto_mail.server.make_board_handler",
            return_value=mock_handler_class,
        ),
        mock.patch(
            "http.server.HTTPServer",
            side_effect=OSError(errno.EADDRINUSE, "Address already in use"),
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve._clear_stale_triage_state",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve._reconcile_loop",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve.threading.Thread",
        ),
    ):
        rc = _cmd_serve(accounts, default_account_id="default", port=8099)

    assert rc == 1


def test_cmd_serve_other_oserror_propagates(
    cfg: MailConfig,
) -> None:
    """Non-EADDRINUSE OSErrors propagate to the caller."""
    from robotsix_auto_mail.cli.commands_serve import _cmd_serve

    accounts = _accounts(cfg)
    mock_handler_class = mock.MagicMock()

    with (
        mock.patch(
            "robotsix_auto_mail.server.make_board_handler",
            return_value=mock_handler_class,
        ),
        mock.patch(
            "http.server.HTTPServer",
            side_effect=OSError(errno.EACCES, "Permission denied"),
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve._clear_stale_triage_state",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve._reconcile_loop",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve.threading.Thread",
        ),
    ):
        with pytest.raises(OSError, match="Permission denied"):
            _cmd_serve(accounts, default_account_id="default", port=8099)


def test_cmd_serve_keyboard_interrupt_returns_0(
    cfg: MailConfig,
) -> None:
    """A KeyboardInterrupt during serve_forever results in a clean exit code 0."""
    from robotsix_auto_mail.cli.commands_serve import _cmd_serve

    accounts = _accounts(cfg)
    mock_handler_class = mock.MagicMock()
    mock_server = mock.MagicMock()
    mock_server.serve_forever.side_effect = KeyboardInterrupt

    with (
        mock.patch(
            "robotsix_auto_mail.server.make_board_handler",
            return_value=mock_handler_class,
        ),
        mock.patch(
            "http.server.HTTPServer",
            return_value=mock_server,
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve._clear_stale_triage_state",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve._reconcile_loop",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_serve.threading.Thread",
        ),
    ):
        rc = _cmd_serve(accounts, default_account_id="default", port=8099)

    assert rc == 0
