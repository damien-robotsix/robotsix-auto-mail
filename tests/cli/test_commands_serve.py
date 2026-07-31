"""Tests for the CLI serve subcommand _cmd_serve entry point."""

from __future__ import annotations

import errno
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from tests.cli.conftest import _accounts

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
            "http.server.ThreadingHTTPServer",
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
            "http.server.ThreadingHTTPServer",
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
            "http.server.ThreadingHTTPServer",
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
            "http.server.ThreadingHTTPServer",
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
            "http.server.ThreadingHTTPServer",
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
            "http.server.ThreadingHTTPServer",
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


def test_cmd_serve_instantiates_threading_http_server(
    cfg: MailConfig,
) -> None:
    """_cmd_serve constructs a ``ThreadingHTTPServer``, not plain ``HTTPServer``."""
    from http.server import HTTPServer, ThreadingHTTPServer

    from robotsix_auto_mail.cli.commands_serve import _cmd_serve

    accounts = _accounts(cfg)
    mock_handler_class = mock.MagicMock()

    # Capture the server instance created by _cmd_serve so we can assert
    # its type.  Mock server_bind / server_activate to avoid real socket
    # operations; mock serve_forever to capture ``self`` and then raise
    # KeyboardInterrupt for a clean exit.
    server_instance = None

    def _capture_serve_forever(self: ThreadingHTTPServer) -> None:
        nonlocal server_instance
        server_instance = self
        raise KeyboardInterrupt

    with (
        mock.patch(
            "robotsix_auto_mail.server.make_board_handler",
            return_value=mock_handler_class,
        ),
        mock.patch.object(HTTPServer, "server_bind"),
        mock.patch.object(HTTPServer, "server_activate"),
        mock.patch.object(
            ThreadingHTTPServer,
            "serve_forever",
            side_effect=_capture_serve_forever,
            autospec=True,
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

    assert server_instance is not None
    assert isinstance(server_instance, ThreadingHTTPServer)
