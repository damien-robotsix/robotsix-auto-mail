"""The mail UI is embedded in the central-deploy dashboard.

robotsix-standards `http-security-headers.md` sets `X-Frame-Options` to
`SAMEORIGIN`, reserving `DENY` for services that are never framed. This one
is framed, so `DENY` breaks the dashboard's same-origin iframe — the page
renders blank with no error visible to the operator.

The same standard requires the `secure` BALANCED preset as the baseline,
defined once (``server/_security_headers.py``) and applied on every response
path, with HSTS (``max-age`` >= 1 year) emitted unconditionally.
"""

from __future__ import annotations

import io
import re
from unittest import mock

from robotsix_auto_mail.server._security_headers import SECURITY_HEADERS
from robotsix_auto_mail.server.handlers import BoardHandler


def _headers() -> dict[str, str]:
    return {name.lower(): value for name, value in SECURITY_HEADERS}


def test_x_frame_options_allows_same_origin_framing() -> None:
    """DENY here blanks the dashboard's iframe."""
    assert _headers()["x-frame-options"] == "SAMEORIGIN"


def test_csp_permits_same_origin_frame_ancestors() -> None:
    """X-Frame-Options alone is not enough — a CSP without frame-ancestors
    still blocks framing in browsers that prefer CSP."""
    csp = _headers()["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'self'" in csp
    # Plain-HTTP loopback/LAN serving must keep loading its own assets.
    assert "upgrade-insecure-requests" not in csp


def test_hsts_is_sent_with_at_least_one_year_max_age() -> None:
    hsts = _headers()["strict-transport-security"]
    match = re.search(r"max-age=(\d+)", hsts)
    assert match is not None
    assert int(match.group(1)) >= 31536000


def test_full_balanced_suite_is_present() -> None:
    headers = _headers()
    for name in (
        "permissions-policy",
        "x-content-type-options",
        "cross-origin-opener-policy",
        "cross-origin-resource-policy",
    ):
        assert name in headers, name
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    # send_response() already writes Server; no duplicate empty one.
    assert "server" not in headers


def _fake_handler() -> BoardHandler:
    handler = BoardHandler.__new__(BoardHandler)
    handler.wfile = io.BytesIO()
    handler._account_cookie = None
    return handler


def _sent_headers(handler: BoardHandler) -> dict[str, str]:
    send_header = handler.send_header
    assert isinstance(send_header, mock.MagicMock)
    return {c.args[0].lower(): c.args[1] for c in send_header.call_args_list}


def test_every_response_path_sets_the_same_security_headers() -> None:
    """Both the body and redirect paths send headers; a fix applied to only
    one leaves the other exposed."""
    expected = _headers()

    def body(h: BoardHandler) -> None:
        h._send_response("ok")

    def redirect(h: BoardHandler) -> None:
        h._redirect("/board", code=302)

    for send in (body, redirect):
        handler = _fake_handler()
        with (
            mock.patch.object(handler, "send_response"),
            mock.patch.object(handler, "send_header"),
            mock.patch.object(handler, "end_headers"),
        ):
            send(handler)
            sent = _sent_headers(handler)
        for name, value in expected.items():
            assert sent.get(name) == value, name
