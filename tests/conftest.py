from __future__ import annotations

import imaplib
import logging
import os
import smtplib
import socket
import sqlite3
import sys as _sys
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest import mock

# ---------------------------------------------------------------------------
# Ensure the venv site-packages are on sys.path so that git-sourced
# dependencies (like robotsix-http) are importable even when the bare
# system Python is used to run tests (CI uses ``uv run``, which handles
# this automatically, but local ``python -m pytest`` does not).
# ---------------------------------------------------------------------------
_venv_site = str(
    Path(__file__).resolve().parent.parent
    / ".venv"
    / "lib"
    / "python3.14"
    / "site-packages"
)
if Path(_venv_site).exists() and _venv_site not in _sys.path:
    _sys.path.insert(0, _venv_site)

import pytest  # noqa: E402

try:
    from hypothesis import settings as _hypothesis_settings

    _has_hypothesis = True
except ImportError:
    _has_hypothesis = False

from robotsix_auto_mail.config import MailConfig  # noqa: E402
from robotsix_auto_mail.db import MailRecord, init_db  # noqa: E402

if _has_hypothesis:
    _hypothesis_settings.register_profile("ci", max_examples=200, deadline=None)
    _hypothesis_settings.register_profile("dev", max_examples=50, deadline=None)
    _hypothesis_settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))


@pytest.fixture(autouse=True)
def _reset_logging_state() -> None:
    """Reset ``robotsix_auto_mail`` / ``robotsix_llmio`` logger state so
    ``caplog`` (and other handlers) work reliably across test modules.

    The logging tests in ``tests/core/test_observability_logging.py`` call
    :func:`robotsix_auto_mail.setup_logging`, which may set
    ``propagate = False`` and install custom handlers.  Without this reset,
    subsequent tests that rely on ``caplog`` (stdlib log capture) may see
    empty ``caplog.records`` even though log lines appear on stdout.
    """
    for logger_name in ("robotsix_auto_mail", "robotsix_llmio"):
        logger = logging.getLogger(logger_name)
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            if hasattr(handler, "close"):
                handler.close()
        logger.setLevel(logging.NOTSET)
        logger.propagate = True


@pytest.fixture(autouse=True)
def _isolate_env() -> Generator[None, None, None]:
    """Strip MAIL_* / LLM_* env vars before each test; restore after."""
    saved: dict[str, str] = {}
    for key in list(os.environ):
        if (
            key.startswith("MAIL_")
            or key.startswith("LLM_")
            or key.startswith("BOARD_AGENT_")
            or key.startswith("LANGFUSE_")
        ):
            saved[key] = os.environ.pop(key)
    yield
    for key, value in saved.items():
        os.environ[key] = value


@pytest.fixture(autouse=True)
def _block_network(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    """Block socket.create_connection so no test accidentally hits the network.

    Tests marked ``@pytest.mark.integration`` are allowed to connect — they
    manage their own network isolation via in-process servers on localhost.
    """
    if request.node.get_closest_marker("integration"):
        yield
        return

    original = socket.create_connection

    def _blocked(address: Any, *args: Any, **kwargs: Any) -> Any:
        # Allow localhost connections for tests that run a local server.
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return original(address, *args, **kwargs)
        raise ConnectionRefusedError(
            "Test attempted a real network connection via socket.create_connection. "
            "Mock the IMAP/SMTP client instead."
        )

    socket.create_connection = _blocked
    yield
    socket.create_connection = original


@pytest.fixture
def cfg() -> MailConfig:
    """A MailConfig with placeholder credentials suitable for most tests."""
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
    )


@pytest.fixture
def conn() -> Generator[sqlite3.Connection, None, None]:
    """In-memory SQLite connection with the application schema applied."""
    c = init_db(":memory:")
    yield c
    c.close()


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    """A file-backed database path inside pytest's tmp_path."""
    return str(tmp_path / "test.db")


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


def _make_record(**overrides: str | int | None) -> MailRecord:
    """Build a ``MailRecord`` with defaults suitable for testing."""
    kwargs: dict[str, str | int | None] = {
        "message_id": "<test@example.com>",
        "sender": "sender@example.com",
        "subject": "Test Subject",
        "date": "2025-06-01T12:00:00Z",
    }
    kwargs.update(overrides)

    def _opt_str(key: str, default: str = "") -> str:
        val = kwargs.get(key, default)
        assert isinstance(val, str)
        return val

    def _opt_int_none(key: str) -> int | None:
        val = kwargs.get(key)
        if val is None:
            return None
        assert isinstance(val, int)
        return val

    return MailRecord(
        message_id=str(kwargs["message_id"]),
        sender=str(kwargs["sender"]),
        subject=str(kwargs["subject"]),
        date=str(kwargs["date"]),
        status=str(kwargs.get("status", "to_read")),
        imap_uid=_opt_int_none("imap_uid"),
        recipients_json=_opt_str("recipients_json", '{"to": [], "cc": []}'),
        body_plain=_opt_str("body_plain", ""),
        body_html=_opt_str("body_html", ""),
        attachments_json=_opt_str("attachments_json", "[]"),
        unsubscribe_header=_opt_str("unsubscribe_header", ""),
        notes=_opt_str("notes", ""),
    )


# ---------------------------------------------------------------------------
# Mock IMAP / SMTP factories
# ---------------------------------------------------------------------------


def _make_mock_imap_ssl() -> mock.MagicMock:
    m = mock.MagicMock(spec=imaplib.IMAP4_SSL)
    m.welcome = b"* OK IMAP4 ready"
    m.capabilities = ("IMAP4rev1", "STARTTLS", "AUTH=PLAIN")
    m.login.return_value = ("OK", [b"Logged in"])
    m.list.return_value = (
        "OK",
        [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasChildren \\Noselect) "/" "[Gmail]"',
        ],
    )
    m.select.return_value = ("OK", [b"5"])
    m.logout.return_value = ("OK", [b"Logged out"])
    m.sock = mock.MagicMock()
    return m


def _make_mock_imap() -> mock.MagicMock:
    """Factory for a mock ``IMAP4`` instance (plain, for STARTTLS / none)."""
    m = mock.MagicMock(spec=imaplib.IMAP4)
    m.login.return_value = ("OK", [b"Logged in"])
    m.list.return_value = ("OK", [])
    m.select.return_value = ("OK", [b"5"])
    m.logout.return_value = ("OK", [b"Logged out"])
    m.starttls.return_value = ("OK", [b"Begin TLS"])
    m.sock = mock.MagicMock()
    return m


def _make_mock_smtp() -> mock.MagicMock:
    m = mock.MagicMock(spec=smtplib.SMTP)
    m.ehlo_resp = b"250-smtp.example.com\n250 STARTTLS"
    m.esmtp_features = {"STARTTLS": "", "AUTH": "PLAIN LOGIN"}
    m.login.return_value = (235, b"2.7.0 Authentication successful")
    m.send_message.return_value = {}
    m.noop.return_value = (250, b"OK")
    return m


def _make_mock_smtp_ssl() -> mock.MagicMock:
    """Factory for a mock ``SMTP_SSL`` instance."""
    m = mock.MagicMock(spec=smtplib.SMTP_SSL)
    m.login.return_value = (235, b"2.7.0 Authentication successful")
    m.send_message.return_value = {}
    m.noop.return_value = (250, b"OK")
    return m
