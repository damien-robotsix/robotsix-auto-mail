"""Minimal HTML sanitizer for email body rendering.

Strips scripts, event handlers, inline ``style`` attributes,
``<style>`` blocks, remote images, and dangerous URL schemes
(``javascript:``, ``data:``, ``vbscript:``) to prevent XSS and
tracking-pixel issues when rendering HTML email parts in the mail
viewer.
"""

from __future__ import annotations

import html
from html.parser import HTMLParser

# -- Allow-listed tags --------------------------------------------------------

_ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "a",
        "abbr",
        "address",
        "article",
        "aside",
        "b",
        "bdi",
        "bdo",
        "blockquote",
        "br",
        "caption",
        "cite",
        "code",
        "col",
        "colgroup",
        "data",
        "dd",
        "del",
        "details",
        "dfn",
        "div",
        "dl",
        "dt",
        "em",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "i",
        "img",
        "ins",
        "kbd",
        "li",
        "main",
        "mark",
        "nav",
        "ol",
        "p",
        "pre",
        "q",
        "rp",
        "rt",
        "ruby",
        "s",
        "samp",
        "section",
        "small",
        "span",
        "strong",
        "sub",
        "summary",
        "sup",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "time",
        "tr",
        "u",
        "ul",
        "var",
        "wbr",
    }
)

# Tags whose entire element (including children) is removed.
_STRIP_TAGS: frozenset[str] = frozenset(
    {
        "applet",
        "base",
        "embed",
        "frame",
        "frameset",
        "iframe",
        "link",
        "meta",
        "noembed",
        "noscript",
        "object",
        "param",
        "script",
        "source",
        "style",
        # Microsoft Office namespace tags
        "o:p",
    }
)

# Tags that are unwrapped — the tag and its attributes are stripped
# but child text/elements are kept.
_UNWRAP_TAGS: frozenset[str] = frozenset(
    {
        "button",
        "form",
        "input",
        "select",
        "textarea",
    }
)

# -- Per-tag allowed attributes -----------------------------------------------

_SAFE_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "title", "rel"}),
    "img": frozenset({"alt", "title", "width", "height"}),
    "td": frozenset({"colspan", "rowspan", "align"}),
    "th": frozenset({"colspan", "rowspan", "align"}),
    "col": frozenset({"span", "width"}),
    "colgroup": frozenset({"span", "width"}),
    "table": frozenset({"border", "cellpadding", "cellspacing"}),
    "abbr": frozenset({"title"}),
    "time": frozenset({"datetime"}),
    "data": frozenset({"value"}),
    "del": frozenset({"cite", "datetime"}),
    "ins": frozenset({"cite", "datetime"}),
    "blockquote": frozenset({"cite"}),
    "q": frozenset({"cite"}),
    "details": frozenset({"open"}),
}

# Attributes allowed on *any* element.
_GLOBAL_ATTRS: frozenset[str] = frozenset({"id", "class", "lang", "dir", "title"})


# -- Public API ---------------------------------------------------------------


def sanitize_html(raw: str) -> str:
    """Sanitize *raw* HTML for safe inline rendering.

    Returns a string of safe HTML with scripts, event handlers and
    remote images stripped.
    """
    parser = _Sanitizer()
    parser.feed(raw)
    parser.close()
    return parser.output


# -- Parser -------------------------------------------------------------------


class _Sanitizer(HTMLParser):
    """Streaming HTML parser that emits sanitized output."""

    # Void elements that appear in _STRIP_TAGS — we skip the tag
    # without entering nesting mode because HTMLParser never emits
    # close events for them.
    _VOID_STRIP: frozenset[str] = frozenset(
        {"base", "embed", "link", "meta", "param", "source"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.output: str = ""
        self._skip_depth: int = 0

    # -- helpers --------------------------------------------------------------

    def _should_skip(self) -> bool:
        return self._skip_depth > 0

    def _skip_enter(self) -> None:
        self._skip_depth += 1

    def _skip_leave(self) -> None:
        if self._skip_depth > 0:
            self._skip_depth -= 1

    # URL schemes that are stripped from href/src attributes.
    _DANGEROUS_SCHEMES: frozenset[str] = frozenset({"javascript", "data", "vbscript"})

    @staticmethod
    def _url_scheme(value: str) -> str:
        """Return the lowercased scheme of *value*, or ``""``."""
        value = value.lstrip()
        # Match up to the first colon, allowing only scheme-legal chars.
        for i, ch in enumerate(value):
            if ch == ":":
                return value[:i].lower()
            if not (ch.isalnum() or ch in "+-."):
                break
        return ""

    def _allowed_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        safe_set = _SAFE_ATTRS.get(tag, frozenset()) | _GLOBAL_ATTRS
        parts: list[str] = []
        for name, value in attrs:
            name_lower = name.lower()
            if name_lower.startswith("on"):
                continue
            if tag == "img" and name_lower == "src":
                continue
            if name_lower not in safe_set:
                continue
            if value is None:
                parts.append(name)
            else:
                # Strip dangerous URL schemes from href attributes.
                if tag == "a" and name_lower == "href":
                    scheme = self._url_scheme(value)
                    if scheme in self._DANGEROUS_SCHEMES:
                        continue
                parts.append(f'{name}="{html.escape(value, quote=True)}"')
        if parts:
            return " " + " ".join(parts)
        return ""

    # -- handler overrides ----------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if self._should_skip():
            self._skip_enter()
            return
        if tag_lower in _STRIP_TAGS:
            if tag_lower in self._VOID_STRIP:
                return
            self._skip_enter()
            return
        if tag_lower in _UNWRAP_TAGS:
            return  # drop the tag, process children normally
        if tag_lower not in _ALLOWED_TAGS:
            return
        attr_str = self._allowed_attrs(tag_lower, attrs)
        self.output += f"<{tag_lower}{attr_str}>"

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if self._should_skip():
            self._skip_leave()
            return
        if tag_lower in _UNWRAP_TAGS:
            return  # tag was unwrapped, drop the close
        if tag_lower not in _ALLOWED_TAGS:
            return
        self.output += f"</{tag_lower}>"

    def handle_data(self, data: str) -> None:
        if self._should_skip():
            return
        self.output += html.escape(data)

    def handle_entityref(self, name: str) -> None:
        if self._should_skip():
            return
        self.output += f"&{name};"

    def handle_charref(self, name: str) -> None:
        if self._should_skip():
            return
        self.output += f"&#{name};"

    def handle_comment(self, data: str) -> None:
        return  # strip all comments

    def handle_decl(self, decl: str) -> None:
        return  # strip <!DOCTYPE ...>

    def handle_pi(self, data: str) -> None:
        return  # strip <?...?>

    def unknown_decl(self, data: str) -> None:
        return  # strip <![CDATA[...]]> etc.
