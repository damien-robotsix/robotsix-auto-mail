"""Fixtures for IMAP integration tests using an in-process IMAP server."""

from __future__ import annotations

import socketserver
import threading
from collections.abc import Generator

import pytest


class TestTCPServer(socketserver.TCPServer):
    """TCPServer that re-raises handler exceptions for visible test failures."""

    def handle_error(self, request, client_address):
        raise


class SimpleIMAPHandler(socketserver.StreamRequestHandler):
    """Minimal IMAP4rev1 handler for in-process integration tests.

    Reads CRLF-terminated lines, dispatches tagged commands to ``_cmd_*``
    methods, and writes tagged/untagged responses back to the client.
    """

    def handle(self) -> None:
        # Greeting
        self.wfile.write(b"* OK IMAP4rev1 Test Server ready\r\n")

        while True:
            line = self.rfile.readline()
            if not line:
                break
            line = line.rstrip(b"\r\n")
            if not line:
                continue

            parts = line.split(None, 2)
            if len(parts) < 2:
                self.wfile.write(b"* BAD Missing tag or command\r\n")
                continue

            tag = parts[0].decode("utf-8", errors="replace")
            cmd = parts[1].upper()
            args_str = (
                parts[2].decode("utf-8", errors="replace") if len(parts) > 2 else ""
            )

            method = getattr(self, f"_cmd_{cmd.decode()}", None)
            if method is not None:
                try:
                    should_break = method(tag, args_str)
                    if should_break:
                        break
                except Exception:
                    raise
            else:
                self._respond(tag, "BAD", "Unknown command")

    # -- response helpers ------------------------------------------------

    def _respond(self, tag: str, status: str, text: str = "") -> None:
        if text:
            response = f"{tag} {status} {text}\r\n"
        else:
            response = f"{tag} {status}\r\n"
        self.wfile.write(response.encode("utf-8"))

    def _untagged(self, text: str) -> None:
        self.wfile.write(f"* {text}\r\n".encode("utf-8"))

    # -- command implementations -----------------------------------------

    def _cmd_CAPABILITY(self, tag: str, _args: str) -> bool:
        self._untagged("CAPABILITY IMAP4rev1 STARTTLS AUTH=PLAIN")
        self._respond(tag, "OK", "CAPABILITY completed")
        return False

    def _cmd_LOGIN(self, tag: str, _args: str) -> bool:
        # Accept any credentials.
        self._respond(tag, "OK", "LOGIN completed")
        return False

    def _cmd_LIST(self, tag: str, _args: str) -> bool:
        self._untagged('LIST (\\HasNoChildren) "/" "INBOX"')
        self._untagged('LIST (\\HasChildren \\Noselect) "/" "[Gmail]"')
        self._respond(tag, "OK", "LIST completed")
        return False

    def _cmd_SELECT(self, tag: str, _args: str) -> bool:
        self._untagged("5 EXISTS")
        self._untagged("0 RECENT")
        self._untagged("FLAGS (\\Seen \\Answered \\Flagged \\Deleted \\Draft)")
        self._respond(tag, "OK", "[READ-WRITE] SELECT completed")
        return False

    def _cmd_LOGOUT(self, tag: str, _args: str) -> bool:
        self._untagged("BYE IMAP4rev1 server logging out")
        self._respond(tag, "OK", "LOGOUT completed")
        return True


@pytest.fixture
def imap_server() -> Generator[tuple[str, int], None, None]:
    """Start an in-process IMAP server on a free port; yield (host, port)."""
    server = TestTCPServer(("127.0.0.1", 0), SimpleIMAPHandler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    yield (host, port)
    server.shutdown()
    server.server_close()
    thread.join(timeout=1.0)
