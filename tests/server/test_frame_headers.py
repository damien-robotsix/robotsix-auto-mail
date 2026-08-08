"""The mail UI is embedded in the central-deploy dashboard.

robotsix-standards `http-security-headers.md` sets `X-Frame-Options` to
`SAMEORIGIN`, reserving `DENY` for services that are never framed. This one
is framed, so `DENY` breaks the dashboard's same-origin iframe — the page
renders blank with no error visible to the operator.
"""

from __future__ import annotations

import ast
from pathlib import Path

HANDLERS = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "robotsix_auto_mail"
    / "server"
    / "handlers.py"
)


def _string_constants() -> list[str]:
    tree = ast.parse(HANDLERS.read_text(encoding="utf-8"))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def test_x_frame_options_allows_same_origin_framing() -> None:
    """DENY here blanks the dashboard's iframe."""
    values = _string_constants()
    assert "SAMEORIGIN" in values
    assert "DENY" not in values


def test_csp_permits_same_origin_frame_ancestors() -> None:
    """X-Frame-Options alone is not enough — a CSP without frame-ancestors
    still blocks framing in browsers that prefer CSP."""
    csps = [v for v in _string_constants() if "default-src" in v]
    assert csps, "no Content-Security-Policy literal found"
    for csp in csps:
        assert "frame-ancestors 'self'" in csp, csp


def test_every_response_path_sets_the_same_frame_policy() -> None:
    """Both the body and redirect paths send headers; a fix applied to only
    one leaves the other blocking."""
    source = HANDLERS.read_text(encoding="utf-8")
    assert source.count('"X-Frame-Options", "SAMEORIGIN"') >= 2
