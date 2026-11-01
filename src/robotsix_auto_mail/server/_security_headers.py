"""Single definition point for the HTTP security-header suite.

robotsix-standards ``http-security-headers.md`` requires the ``secure``
library's BALANCED preset as the baseline, defined once and applied on
every response path.  ``SecureASGIMiddleware`` does not apply to the
stdlib ``http.server`` board, so the framework-agnostic header object is
rendered here into a plain ``(name, value)`` tuple that
:meth:`~robotsix_auto_mail.server.handlers.BoardHandler._send_security_headers`
writes on both the body and the redirect path.

Deviations from the preset (each tightens or preserves existing behaviour):

* ``Referrer-Policy: no-referrer`` — stricter than the preset's
  ``strict-origin-when-cross-origin``; mail URLs can carry message ids.
* ``Server`` is dropped — the preset emits an empty value, but
  :meth:`http.server.BaseHTTPRequestHandler.send_response` already writes a
  ``Server`` header and a duplicate empty one would be ambiguous.
* The CSP's ``upgrade-insecure-requests`` directive is dropped — the board
  is also served over plain HTTP on loopback/LAN, where upgrading
  sub-resource fetches to ``https`` would break every asset.  HSTS is still
  sent unconditionally (browsers ignore it over plain HTTP).

``X-Frame-Options: SAMEORIGIN`` and ``frame-ancestors 'self'`` come from
the preset unchanged: the UI is framed by the central-deploy dashboard,
so ``DENY`` / ``'none'`` must never be used.
"""

from __future__ import annotations

from secure import Preset, Secure

__all__ = ["SECURITY_HEADERS"]

_DROPPED_HEADERS = frozenset({"server"})
_DROPPED_CSP_DIRECTIVES = frozenset({"upgrade-insecure-requests"})
_OVERRIDES = {"Referrer-Policy": "no-referrer"}


def _strip_csp_directives(policy: str) -> str:
    directives = [d.strip() for d in policy.split(";") if d.strip()]
    kept = [
        d for d in directives if d.split()[0].lower() not in _DROPPED_CSP_DIRECTIVES
    ]
    return "; ".join(kept)


def _build_security_headers() -> tuple[tuple[str, str], ...]:
    headers: list[tuple[str, str]] = []
    for name, value in Secure.from_preset(Preset.BALANCED).headers.items():
        if name.lower() in _DROPPED_HEADERS:
            continue
        if name.lower() == "content-security-policy":
            value = _strip_csp_directives(value)
        headers.append((name, _OVERRIDES.get(name, value)))
    return tuple(headers)


SECURITY_HEADERS: tuple[tuple[str, str], ...] = _build_security_headers()
"""The security-header suite sent on every response, as ``(name, value)``."""
