"""Unit tests for ``robotsix_auto_mail.server.board_agent`` — the
board-agent lifecycle (start/stop background daemon thread).

Covers:
- ``start_board_agent`` returns ``None`` when the optional
  ``robotsix-board-agent`` dependency is missing (ImportError).
- ``start_board_agent`` returns a handle when the dependency is
  available.
- ``stop_board_agent(None)`` is a no-op.
- ``stop_board_agent(handle)`` signals the stop event and joins the
  thread.
- The ``board_agent_enabled`` config guard gates the start call inside
  ``cli.commands._cmd_serve``.
"""

from __future__ import annotations

import threading
from typing import cast
from unittest import mock

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.server.board_agent import start_board_agent, stop_board_agent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: object) -> MailConfig:
    """Return a ``MailConfig`` with all board-agent fields populated."""
    kwargs: dict[str, object] = dict(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        board_agent_enabled=True,
        board_agent_api_url="https://board.example.com/api",
        board_agent_api_token="tok-123",
        board_agent_repo_id="repo-1",
        board_agent_write_ops=True,
    )
    kwargs.update(overrides)
    return MailConfig(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# start_board_agent — ImportError path
# ---------------------------------------------------------------------------


def test_start_board_agent_import_error_returns_none(cfg: MailConfig) -> None:
    """When the optional dependency cannot be imported, return ``None``."""
    with mock.patch.dict(
        "sys.modules",
        {"robotsix_board_agent": None, "robotsix_agent_comm": None},
    ):
        result = start_board_agent(cfg)
    assert result is None


# ---------------------------------------------------------------------------
# start_board_agent — happy path
# ---------------------------------------------------------------------------


async def _async_noop() -> None:
    """Coroutine that returns immediately."""


def test_start_board_agent_returns_handle_when_dependency_available(
    cfg: MailConfig,
) -> None:
    """When the dependency is importable, a (Thread, Event) handle is returned."""
    fake_board_agent_cls = mock.MagicMock()
    fake_board_agent = mock.MagicMock()
    # agent.start() and agent.stop() must be awaitable.
    fake_board_agent.start.return_value = _async_noop()
    fake_board_agent.stop.return_value = _async_noop()
    fake_board_agent_cls.return_value = fake_board_agent

    fake_registry = mock.MagicMock()
    fake_settings_cls = mock.MagicMock()

    with mock.patch.dict(
        "sys.modules",
        {
            "robotsix_board_agent": mock.MagicMock(
                BoardAgent=fake_board_agent_cls,
                BoardAgentSettings=fake_settings_cls,
            ),
            "robotsix_agent_comm": mock.MagicMock(Registry=fake_registry),
        },
    ):
        handle = start_board_agent(cfg)

    assert handle is not None
    thread, stop_event = cast("tuple[threading.Thread, threading.Event]", handle)
    assert isinstance(thread, threading.Thread)
    assert isinstance(stop_event, threading.Event)
    assert thread.daemon is True
    assert thread.is_alive()

    # Clean up: signal stop so the daemon thread exits.
    stop_event.set()
    thread.join(timeout=5.0)


# ---------------------------------------------------------------------------
# stop_board_agent — no-op on None
# ---------------------------------------------------------------------------


def test_stop_board_agent_none_is_noop() -> None:
    """Calling with ``None`` must not raise."""
    # Should not raise.
    stop_board_agent(None)


# ---------------------------------------------------------------------------
# stop_board_agent — signals stop event
# ---------------------------------------------------------------------------


def test_stop_board_agent_signals_event_and_joins_thread() -> None:
    """stop_board_agent sets the stop event and joins the thread."""
    stop_event = threading.Event()
    # Use a simple target that waits on the event so join() returns.
    thread = threading.Thread(
        target=lambda: stop_event.wait(),
        daemon=True,
    )
    thread.start()

    stop_board_agent((thread, stop_event))
    assert stop_event.is_set()
    assert not thread.is_alive()


def test_stop_board_agent_handles_cleanup_exceptions() -> None:
    """stop_board_agent must not raise even when join() fails."""
    stop_event = threading.Event()
    bad_thread = mock.MagicMock(spec=threading.Thread)
    bad_thread.join.side_effect = RuntimeError("boom")

    # Must not raise.
    stop_board_agent((bad_thread, stop_event))
    assert stop_event.is_set()
    bad_thread.join.assert_called_once_with(timeout=5.0)


# ---------------------------------------------------------------------------
# board_agent_enabled gate in cli.commands.serve_board
# ---------------------------------------------------------------------------


def test_serve_board_gate_respects_board_agent_enabled() -> None:
    """_cmd_serve only calls start_board_agent when board_agent_enabled is True."""
    from robotsix_auto_mail.cli.commands_serve import _cmd_serve

    with (
        mock.patch(
            "robotsix_auto_mail.server.board_agent.start_board_agent"
        ) as mock_start,
        mock.patch(
            "robotsix_auto_mail.server.board_agent.stop_board_agent"
        ) as mock_stop,
        mock.patch("robotsix_auto_mail.server.make_board_handler") as mock_make_handler,
        mock.patch("robotsix_auto_mail.cli.commands_serve._clear_stale_triage_state"),
        mock.patch("robotsix_auto_mail.cli.commands_serve._reconcile_loop"),
        mock.patch("http.server.HTTPServer") as mock_http_server,
        mock.patch("robotsix_auto_mail.cli.commands_serve.threading.Thread"),
    ):
        # --- enabled=True (default from _make_config) ---
        cfg_enabled = _make_config()
        accounts = mock.MagicMock()
        accounts.get.return_value = mock.MagicMock(config=cfg_enabled)

        # Trigger KeyboardInterrupt to exit serve_forever immediately.
        mock_server_instance = mock.MagicMock()
        mock_server_instance.serve_forever.side_effect = KeyboardInterrupt
        mock_http_server.return_value = mock_server_instance
        mock_make_handler.return_value = mock.MagicMock()

        _cmd_serve(accounts=accounts, default_account_id="default", port=0)
        mock_start.assert_called_once()
        mock_stop.assert_called_once()

        mock_start.reset_mock()
        mock_stop.reset_mock()

        # --- enabled=False ---
        cfg_disabled = _make_config(board_agent_enabled=False)
        accounts.get.return_value = mock.MagicMock(config=cfg_disabled)

        _cmd_serve(accounts=accounts, default_account_id="default", port=0)
        mock_start.assert_not_called()
        mock_stop.assert_called_once()  # still called, just with None
