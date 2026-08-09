"""Safe HTML sanitizer for rendering email HTML bodies.

Strips scripts, event handlers, remote images, and other active
content while preserving structural markup so the email body can
be rendered inline in the board detail view without XSS or
tracking-pixel risks.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

# -- Tag / attribute allow-lists ------------------------------------------------

_VOID_ELEMENTS: frozenset[str] = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)

_ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "a",
        "b",
        "blockquote",
        "br",
        "code",
        "dd",
        "div",
        "dl",
        "dt",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "img",
        "li",
        "ol",
        "p",
        "pre",
        "span",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
)

# Per-tag allowed attributes.  Tags not in this dict get NO attributes.
_ALLOWED_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "title"}),
    "img": frozenset({"alt", "width", "height"}),
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan"}),
}

# Tags whose entire element tree is stripped (element + all descendants).
_SKIP_TAGS: frozenset[str] = frozenset(
    {
        "script",
        "style",
        "iframe",
        "object",
        "embed",
        "link",
        "meta",
        "noscript",
        "applet",
    }
)

_SAFE_URL_SCHEMES: frozenset[str] = frozenset({"http:", "https:", "mailto:"})

# Regex that matches an attribute value containing a dangerous javascript:
# or data: URL, ignoring leading whitespace.
_DANGEROUS_URL_RE: re.Pattern[str] = re.compile(
    r"^\s*(javascript|data|vbscript)\s*:", re.IGNORECASE
)


def _is_safe_url(value: str) -> bool:
    """Return True when *value* uses an allow-listed URL scheme."""
    if _DANGEROUS_URL_RE.match(value):
        return False
    lower = value.strip().lower()
    return any(lower.startswith(scheme) for scheme in _SAFE_URL_SCHEMES)


class _SanitizingParser(HTMLParser):
    """HTMLParser subclass that rebuilds a safe subset of its input."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth > 0:
            self._skip_depth += 1
            return

        tag_lower = tag.lower()
        if tag_lower in _SKIP_TAGS:
            self._skip_depth = 1
            return

        if tag_lower not in _ALLOWED_TAGS:
            return

        allowed_attr_names = _ALLOWED_ATTRS.get(tag_lower, frozenset())
        safe_attrs: list[str] = []
        for name, value in attrs:
            if value is None:
                continue
            name_lower = name.lower()
            if name_lower.startswith("on"):
                continue
            if name_lower not in allowed_attr_names:
                continue
            if name_lower == "href" and tag_lower == "a" and not _is_safe_url(value):
                continue
            if name_lower == "src" and tag_lower == "img":
                # Block remote images by stripping the src attribute entirely.
                continue
            safe_attrs.append(f'{name}="{html.escape(value, quote=True)}"')

        attrs_str = (" " + " ".join(safe_attrs)) if safe_attrs else ""
        self._parts.append(f"<{tag}{attrs_str}>")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth > 0:
            self._skip_depth -= 1
            return
        tag_lower = tag.lower()
        if tag_lower not in _ALLOWED_TAGS or tag_lower in _VOID_ELEMENTS:
            return
        self._parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        self._parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if self._skip_depth > 0:
            return
        self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._skip_depth > 0:
            return
        self._parts.append(f"&#{name};")

    def get_result(self) -> str:
        return "".join(self._parts)


def sanitize_html(raw: str) -> str:
    """Return *raw* with unsafe elements and attributes removed.

    Strips ``<script>``, ``<style>``, ``<iframe>``, and similar
    active-content tags (including their descendants).  Removes
    event-handler attributes (``on*``), javascript: / data: URLs,
    and ``<img src>`` (to block tracking pixels).

    Returns an empty string when *raw* is empty or whitespace-only.
    """
    if not raw or not raw.strip():
        return ""
    parser = _SanitizingParser()
    parser.feed(raw)
    parser.close()
    return parser.get_result()
