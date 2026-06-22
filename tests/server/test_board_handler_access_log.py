"""Tests for BoardHandler.log_message access logging."""

from __future__ import annotations

import logging
import socket
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    pass


class _FakeRequest:
    """Minimal fake to satisfy BaseHTTPRequestHandler.__init__."""

    def makefile(self, *args: object, **kwargs: object) -> object:
        return self

    def readline(self, *args: object, **kwargs: object) -> bytes:
        return b""

    def close(self) -> None:
        pass


def test_log_message_logs_info(caplog: pytest.LogCaptureFixture) -> None:
    """log_message routes to the robotsix_auto_mail.http.access logger at INFO."""
    from robotsix_auto_mail.server.handlers import BoardHandler

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        _, port = sock.getsockname()
        sock.listen(1)
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.connect(("127.0.0.1", port))
            server_sock, _ = sock.accept()
            try:
                handler = BoardHandler(
                    _FakeRequest(),
                    ("192.168.1.42", 12345),
                    None,
                    db_path=":memory:",
                )
                with caplog.at_level(
                    logging.INFO, logger="robotsix_auto_mail.http.access"
                ):
                    handler.log_message(
                        '"%s %s %s" %s %s',
                        "GET",
                        "/board",
                        "HTTP/1.1",
                        "200",
                        "1234",
                    )
            finally:
                server_sock.close()
        finally:
            client.close()
    finally:
        sock.close()

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.INFO
    assert record.name == "robotsix_auto_mail.http.access"
    assert "192.168.1.42" in record.message
    assert '"GET /board HTTP/1.1" 200 1234' in record.message
