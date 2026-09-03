"""Inline IMAP FETCH response parsing helpers.

Decodes inline (non-literal) IMAP ``FETCH`` response lines into flat
dicts.  ``imaplib`` returns inline FETCH responses as bare ``bytes``
items; these helpers split the parenthesised attribute list into
``flags`` / ``internal_date`` / ``size`` / envelope fields (``subject``,
``from``, ``to``, ``date``, ``message_id``) without round-tripping
through a full RFC 3501 parser.

Owned by :mod:`robotsix_auto_mail.imap.client` — the ``ImapClient``
class calls these via :func:`_parse_inline_fetch_attrs` when it builds
envelope metadata.
"""

from __future__ import annotations

import contextlib


def _parse_inline_fetch_attrs(line: bytes) -> dict[str, object] | None:
    """Parse an inline IMAP FETCH response line into a flat dict.

    ``imaplib`` returns inline FETCH responses (no literals) as bare
    ``bytes`` items, e.g.::

        b'1 (FLAGS (\\Seen) INTERNALDATE "01-Jan-2024 ..." '
        b'RFC822.SIZE 1234 ENVELOPE ("01-Jan-2024 ..." "Hello" '
        b'(("Alice" NIL "user" "example.com")) NIL NIL NIL NIL NIL NIL NIL))'

    This function parses the key-value pairs from the parenthesised
    part after the sequence number.  Returns a dict with keys
    ``"flags"``, ``"internal_date"``, ``"size"``, ``"subject"``,
    ``"from"``, ``"to"``, ``"date"``, ``"message_id"``, or ``None``
    on parse failure.
    """
    text = line.decode("utf-8", errors="replace")
    # Strip the sequence number prefix: "1 (FLAGS ...)"
    idx = text.find("(")
    if idx < 0:
        return None
    text = text[idx:]  # "(FLAGS ...)"
    if not text.startswith("(") or not text.endswith(")"):
        return None
    inner = text[1:-1]  # FLAGS (\Seen) INTERNALDATE "..." ENVELOPE (...)

    result: dict[str, object] = {
        "flags": [],
        "internal_date": "",
        "size": 0,
        "subject": "",
        "from": "",
        "to": "",
        "date": "",
        "message_id": "",
    }

    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == " ":
            i += 1
            continue
        # Read the key (atom).
        j = i
        while j < len(inner) and inner[j] not in (" ", ")"):
            j += 1
        key = inner[i:j].upper()
        i = j

        # Skip whitespace before value.
        while i < len(inner) and inner[i] == " ":
            i += 1

        if i >= len(inner):
            break

        # Parse the value.
        if inner[i] == "(":
            # Parenthesised value — read matching close paren.
            depth = 1
            j = i + 1
            while j < len(inner) and depth > 0:
                if inner[j] == "(":
                    depth += 1
                elif inner[j] == ")":
                    depth -= 1
                j += 1
            val_text = inner[i + 1 : j - 1]  # content between outer parens
            i = j

            if key == "FLAGS":
                result["flags"] = _parse_flags(val_text)
            elif key == "ENVELOPE":
                env = _parse_envelope_inline(val_text)
                if env:
                    result["subject"] = env.get("subject", "")
                    result["from"] = env.get("from", "")
                    result["to"] = env.get("to", "")
                    result["date"] = env.get("date", "")
                    result["message_id"] = env.get("message_id", "")
        elif inner[i] == '"':
            # Quoted string.
            j = i + 1
            while j < len(inner) and inner[j] != '"':
                j += 1
            val_text = inner[i + 1 : j]
            i = j + 1

            if key == "INTERNALDATE":
                result["internal_date"] = val_text
                if not result["date"]:
                    result["date"] = val_text
        else:
            # Bare atom (like RFC822.SIZE value, or NIL).
            j = i
            while j < len(inner) and inner[j] not in (" ", ")"):
                j += 1
            val_text = inner[i:j]
            i = j

            if key == "RFC822.SIZE":
                with contextlib.suppress(ValueError):
                    result["size"] = int(val_text)

    return result


def _parse_flags(text: str) -> list[str]:
    """Parse a FLAGS list like ``\\Seen \\Answered`` into a list of strings."""
    flags: list[str] = []
    for token in text.split():
        token = token.strip()
        if token:
            flags.append(token)
    return flags


def _parse_envelope_inline(text: str) -> dict[str, str]:
    """Parse an inline ENVELOPE structure into a dict with subject/from/date/message_id.

    The ENVELOPE is: ``date subject from sender reply-to to cc bcc
    in-reply-to message-id`` where each field is either a quoted
    string, NIL, or a parenthesised address list.
    """
    result: dict[str, str] = {}

    def _read_field(s: str, pos: int) -> tuple[str, int]:
        """Read one field value starting at *pos*, return (value, next_pos)."""
        while pos < len(s) and s[pos] == " ":
            pos += 1
        if pos >= len(s):
            return "", pos
        if s[pos] == '"':
            # Quoted string.
            j = pos + 1
            while j < len(s) and s[j] != '"':
                j += 1
            val = s[pos + 1 : j]
            return val, j + 1
        elif s[pos : pos + 3] == "NIL":
            return "", pos + 3
        elif s[pos] == "(":
            # Address list: ((personal NIL mailbox host) ...)
            depth = 1
            j = pos + 1
            while j < len(s) and depth > 0:
                if s[j] == "(":
                    depth += 1
                elif s[j] == ")":
                    depth -= 1
                j += 1
            addr_text = s[pos + 1 : j - 1]
            return _format_first_address(addr_text), j
        else:
            # Bare atom — read until space or end.
            j = pos
            while j < len(s) and s[j] != " ":
                j += 1
            return s[pos:j], j

    s = text
    pos = 0
    date_str, pos = _read_field(s, pos)
    result["date"] = date_str
    subject, pos = _read_field(s, pos)
    result["subject"] = subject
    from_str, pos = _read_field(s, pos)
    result["from"] = from_str
    # ENVELOPE field order (RFC 3501): date subject from sender reply-to
    # to cc bcc in-reply-to message-id.  Skip sender and reply-to, then
    # read the "to" recipient — the load-bearing field for Sent messages,
    # where "from" is always the account itself.
    _sender, pos = _read_field(s, pos)
    _reply_to, pos = _read_field(s, pos)
    to_str, pos = _read_field(s, pos)
    result["to"] = to_str
    # Skip cc, bcc, in-reply-to; read message-id (last field).
    _cc, pos = _read_field(s, pos)
    _bcc, pos = _read_field(s, pos)
    _in_reply_to, pos = _read_field(s, pos)
    message_id, pos = _read_field(s, pos)
    result["message_id"] = message_id

    return result


def _format_first_address(addr_text: str) -> str:
    """Parse the first address from an IMAP address list and format it.

    An address list looks like: ``("Personal" NIL "mailbox" "host")``
    or multiple: ``(...)(...)``.
    """
    if not addr_text.strip():
        return ""
    # Find the first address tuple.
    text = addr_text.strip()
    if text.startswith("("):
        # Find the matching close paren for the first address.
        depth = 1
        j = 1
        while j < len(text) and depth > 0:
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
            j += 1
        addr_inner = text[1 : j - 1]
    else:
        addr_inner = text

    # addr_inner: "personal" NIL "mailbox" "host"
    parts: list[str] = []
    i = 0
    while i < len(addr_inner):
        if addr_inner[i] == " ":
            i += 1
            continue
        if addr_inner[i] == '"':
            j = i + 1
            while j < len(addr_inner) and addr_inner[j] != '"':
                j += 1
            parts.append(addr_inner[i + 1 : j])
            i = j + 1
        elif addr_inner[i : i + 3] == "NIL":
            parts.append("")
            i += 3
        else:
            # Bare atom.
            j = i
            while j < len(addr_inner) and addr_inner[j] != " ":
                j += 1
            parts.append(addr_inner[i:j])
            i = j

    # Address tuple is (personal, at_domain, mailbox, host)
    if len(parts) >= 4:
        personal = parts[0]
        mailbox = parts[2]
        host = parts[3]
        if personal:
            return f"{personal} <{mailbox}@{host}>"
        if mailbox and host:
            return f"{mailbox}@{host}"
    return ""
