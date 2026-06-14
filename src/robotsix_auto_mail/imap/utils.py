"""Pure encoding utilities for IMAP mailbox names.

Modified UTF-7 (RFC 3501 §5.1.3) encode/decode helpers plus mailbox-name
quoting.  These have no dependency on ``ImapClient`` or ``MailboxInfo``.
"""

from __future__ import annotations

import base64
import re


def _is_gmail_host(host: str) -> bool:
    """Return ``True`` when *host* is a Google / Gmail IMAP endpoint.

    Covers both consumer Gmail and Google Workspace, which share the
    ``imap.gmail.com`` / ``imap.googlemail.com`` hosts.
    """
    normalized = host.strip().rstrip(".").lower()
    return normalized.endswith("gmail.com") or normalized.endswith("googlemail.com")


# ---------------------------------------------------------------------------
# Modified UTF-7 (RFC 3501 §5.1.3) — IMAP mailbox-name encoding
# ---------------------------------------------------------------------------
#
# stdlib ``imaplib`` encodes command arguments as ASCII, so a mailbox name
# with non-ASCII characters (e.g. ``Préfecture``) raises ``UnicodeEncodeError``
# before it ever reaches the wire.  IMAP mandates "modified UTF-7": printable
# ASCII passes through verbatim (with ``&`` written ``&-``); any other run of
# characters is the modified-BASE64 of its UTF-16BE bytes, delimited by ``&``
# and ``-``, using ``,`` in place of ``/``.  Pure-ASCII names without ``&`` are
# unchanged, so encoding is a no-op for the common case.


def _modified_b64encode(text: str) -> str:
    encoded = base64.b64encode(text.encode("utf-16-be")).decode("ascii")
    return encoded.rstrip("=").replace("/", ",")


def _modified_b64decode(chunk: str) -> str:
    data = chunk.replace(",", "/")
    data += "=" * (-len(data) % 4)  # restore stripped BASE64 padding
    return base64.b64decode(data).decode("utf-16-be")


def imap_utf7_encode(name: str) -> str:
    """Encode a mailbox *name* to IMAP modified UTF-7."""
    out: list[str] = []
    run: list[str] = []

    def _flush() -> None:
        if run:
            out.append("&" + _modified_b64encode("".join(run)) + "-")
            run.clear()

    for ch in name:
        if 0x20 <= ord(ch) <= 0x7E:
            _flush()
            out.append("&-" if ch == "&" else ch)
        else:
            run.append(ch)
    _flush()
    return "".join(out)


def imap_utf7_decode(name: str) -> str:
    """Decode an IMAP modified-UTF-7 mailbox *name* back to Unicode."""
    if "&" not in name:
        return name  # fast path: nothing to decode
    out: list[str] = []
    i = 0
    while i < len(name):
        ch = name[i]
        if ch != "&":
            out.append(ch)
            i += 1
            continue
        end = name.find("-", i + 1)
        if end == -1:  # malformed; pass the remainder through verbatim
            out.append(name[i:])
            break
        chunk = name[i + 1 : end]
        out.append("&" if chunk == "" else _modified_b64decode(chunk))
        i = end + 1
    return "".join(out)


# A mailbox name made only of these chars is a bare IMAP "atom" and may be
# sent verbatim.  Anything else — most importantly a SPACE, as in Gmail's
# ``[Gmail]/All Mail`` / ``[Gmail]/Sent Mail`` — must be sent as a quoted
# string, because stdlib ``imaplib`` does NOT quote mailbox names and the
# server then rejects the command ("Could not parse command").
_ATOM_SAFE_RE = re.compile(r"[A-Za-z0-9._/-]+")


def _encode_mailbox(name: str) -> str:
    """Return *name* ready to pass to an imaplib mailbox command.

    Encodes to modified UTF-7 (so non-ASCII names work) and wraps the result
    in an IMAP quoted string unless it is a bare atom — covering spaces,
    brackets and other non-atom characters that imaplib would otherwise send
    unquoted and unparseable.
    """
    encoded = imap_utf7_encode(name)
    if encoded and _ATOM_SAFE_RE.fullmatch(encoded):
        return encoded
    escaped = encoded.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
