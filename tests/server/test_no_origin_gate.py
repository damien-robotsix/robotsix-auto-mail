"""Regression tests: the board server ships no component-level CSRF gate.

Per robotsix-standards ``component-standard.md``, authentication is
centralized — components ship none.  The ``Origin`` / ``Host`` /
``X-Forwarded-Host`` matching guard (``BoardHandler._check_csrf``) and
the ``trusted_origins`` allowlist were removed; cross-site request defence
is the edge's job (the SSO session cookie is ``SameSite``-bound there).

These tests lock that decision in: a POST carrying a foreign ``Origin``
header must reach the route handler like any other request — never be
rejected with a component-level 403.
"""

from __future__ import annotations

import urllib.parse
import urllib.request

import pytest


def _post(port: int, *, origin: str | None, path: str = "/move") -> tuple[int, str]:
    """POST an empty form to *path* with an optional ``Origin`` header."""
    from tests.server.conftest_helpers import CaptureError, NoRedirect

    opener = urllib.request.build_opener(NoRedirect(), CaptureError())
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if origin is not None:
        headers["Origin"] = origin
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=urllib.parse.urlencode({}).encode("utf-8"),
        headers=headers,
    )  # noqa: S310
    resp = opener.open(req)
    return resp.status, resp.read().decode("utf-8")


class TestNoComponentCsrfGate:
    """Cross-origin POSTs are not rejected by the component.

    With the CSRF guard removed, every POST below must get past origin
    matching (``/move`` returns 400 "missing message_id", not 403).
    """

    @pytest.fixture(autouse=True)
    def setup(self, single_db: str) -> None:
        """Start a test server bound to an ephemeral port."""
        from tests.server.conftest_helpers import _start_test_server

        self.server, self.port = _start_test_server(single_db)

    def teardown_method(self) -> None:
        self.server.shutdown()

    def test_foreign_origin_not_rejected(self) -> None:
        """A POST with an external Origin header must not receive 403."""
        status, body = _post(self.port, origin="http://evil.example.com")
        assert status == 400  # reached the route; missing message_id
        assert "cross-origin" not in body.lower()

    def test_null_origin_cross_site_not_rejected(self) -> None:
        """``Origin: null`` from a sandboxed cross-site context is not 403'd."""
        status, body = _post(self.port, origin="null")
        assert status != 403
        assert "cross-origin" not in body.lower()

    def test_proxy_rewritten_host_not_rejected(self) -> None:
        """A proxied POST with a public Origin and rewritten Host is not 403'd.

        This is the exact production shape that repeatedly broke behind the
        reverse proxy (batch-delete, add-account): the edge sets
        ``Origin: https://mail.deploy.robotsix.net`` while the backend sees
        ``Host: backend.internal``.  With the guard removed the request must
        pass straight through.
        """
        status, body = _post(self.port, origin="https://mail.deploy.robotsix.net")
        assert status == 400
        assert "cross-origin" not in body.lower()

    def test_batch_delete_with_foreign_origin_not_rejected(self) -> None:
        """``/batch-delete`` must accept a POST from a foreign origin."""
        status, body = _post(
            self.port, origin="https://mail.deploy.robotsix.net", path="/batch-delete"
        )
        assert status != 403
        assert "cross-origin" not in body.lower()
