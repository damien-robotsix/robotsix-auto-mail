"""CSRF protection tests for the board server.

Covers the ``Origin``-header guard in ``BoardHandler._check_csrf`` and
the ``--host`` flag forwarding in ``_cmd_serve``.
"""

from __future__ import annotations

import os
import tempfile
import urllib.parse
import urllib.request
from unittest import mock

import pytest

from robotsix_auto_mail.cli.commands_serve import _cmd_serve
from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _post_with_origin(
    port: int,
    *,
    origin: str | None,
    path: str = "/move",
    fields: dict[str, str] | None = None,
) -> tuple[int, str]:
    """POST url-encoded *fields* to *path* with an optional ``Origin`` header.

    Returns ``(status, body)``.  Does not follow redirects and captures
    error responses so 400/403/404 can be inspected directly.
    """
    if fields is None:
        fields = {}
    data = urllib.parse.urlencode(fields).encode("utf-8")
    url = f"http://127.0.0.1:{port}{path}"

    # Same opener that the rest of the server tests use: no redirect
    # following, error responses returned as normal responses.
    from tests.server.conftest_helpers import CaptureError, NoRedirect

    opener = urllib.request.build_opener(NoRedirect(), CaptureError())
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if origin is not None:
        headers["Origin"] = origin
    req = urllib.request.Request(url, data=data, headers=headers)  # noqa: S310
    resp = opener.open(req)
    body = resp.read().decode("utf-8")
    return resp.status, body


def _account_config(db_path: str) -> MailConfig:
    """A minimal ``MailConfig`` for the unit tests below."""
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="me@example.com",
        password="s3cret",
        db_path=db_path,
        archive_enabled=False,
        triage_on_ingest=False,
    )


# ---------------------------------------------------------------------------
# integration tests against a live test server
# ---------------------------------------------------------------------------


def _start_csrf_server(db_path: str) -> tuple[object, int]:
    """Start a test server wired to *db_path*; return ``(server, port)``."""
    from tests.server.conftest_helpers import _start_test_server

    return _start_test_server(db_path)


class TestCsrfIntegration:
    """CSRF gate integration tests against a live ``HTTPServer``."""

    @pytest.fixture(autouse=True)
    def setup(self, single_db: str) -> None:
        """Start a test server bound to an ephemeral port."""
        self.server, self.port = _start_csrf_server(single_db)

    def teardown_method(self) -> None:
        self.server.shutdown()

    # -- cross-origin -------------------------------------------------------

    def test_cross_origin_rejected(self) -> None:
        """A POST with an external Origin header must receive 403."""
        status, body = _post_with_origin(
            self.port,
            origin="http://evil.example.com",
        )
        assert status == 403
        assert "cross-origin" in body.lower()

    # -- same-origin (127.0.0.1) ---------------------------------------------

    def test_same_origin_127_allowed(self) -> None:
        """A POST with the server's own ``127.0.0.1`` origin must pass CSRF."""
        status, body = _post_with_origin(
            self.port,
            origin=f"http://127.0.0.1:{self.port}",
        )
        # Missing ``message_id`` → 400, NOT 403 (CSRF gate passed).
        assert status == 400
        assert "cross-origin" not in body.lower()

    # -- same-origin (localhost) ---------------------------------------------

    def test_same_origin_localhost_allowed(self) -> None:
        """A POST with the server's own ``localhost`` origin must pass CSRF."""
        status, body = _post_with_origin(
            self.port,
            origin=f"http://localhost:{self.port}",
        )
        assert status == 400
        assert "cross-origin" not in body.lower()

    # -- missing Origin header -----------------------------------------------

    def test_missing_origin_allowed(self) -> None:
        """A POST without an ``Origin`` header must pass CSRF."""
        status, body = _post_with_origin(
            self.port,
            origin=None,
        )
        assert status == 400
        assert "cross-origin" not in body.lower()


# ---------------------------------------------------------------------------
# unit tests: _cmd_serve passes ``host`` to ``HTTPServer``
# ---------------------------------------------------------------------------


class TestCmdServeHost:
    """Verify that ``_cmd_serve`` forwards ``host`` to ``HTTPServer``."""

    def test_host_defaults_to_loopback(self) -> None:
        """``_cmd_serve`` constructs ``HTTPServer`` with 127.0.0.1 by default."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            cfg = _account_config(db_path)
            accounts = MailAccountsConfig(
                accounts=(MailAccount(account_id="A", config=cfg, label=None),),
                default_account_id="A",
            )
            with (
                mock.patch("http.server.ThreadingHTTPServer") as m,
                mock.patch(
                    "robotsix_auto_mail.cli.commands_serve._clear_stale_triage_state"
                ),
            ):
                instance = mock.MagicMock()
                m.return_value = instance
                _cmd_serve(accounts, default_account_id="A", port=9999)
                m.assert_called_once_with(("127.0.0.1", 9999), mock.ANY)
        finally:
            os.unlink(db_path)

    def test_host_0_0_0_0_forwarded(self) -> None:
        """``_cmd_serve`` constructs ``HTTPServer`` with 0.0.0.0 when passed."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            cfg = _account_config(db_path)
            accounts = MailAccountsConfig(
                accounts=(MailAccount(account_id="A", config=cfg, label=None),),
                default_account_id="A",
            )
            with (
                mock.patch("http.server.ThreadingHTTPServer") as m,
                mock.patch(
                    "robotsix_auto_mail.cli.commands_serve._clear_stale_triage_state"
                ),
            ):
                instance = mock.MagicMock()
                m.return_value = instance
                _cmd_serve(
                    accounts,
                    default_account_id="A",
                    port=9999,
                    host="0.0.0.0",  # noqa: S104  # nosec B104
                )
                m.assert_called_once_with(("0.0.0.0", 9999), mock.ANY)  # noqa: S104  # nosec B104
        finally:
            os.unlink(db_path)
