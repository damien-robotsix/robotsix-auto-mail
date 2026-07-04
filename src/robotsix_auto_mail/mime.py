"""Pure functions for constructing plain-text MIME email messages.

This module isolates MIME construction from SMTP transport so the
message builder can be tested without instantiating an SMTP client
and reused by any caller that needs to compose a plain-text email.
"""

from __future__ import annotations

from email.mime.text import MIMEText
from email.utils import formatdate


def build_plain_text_message(
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    *,
    cc: list[str] | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> MIMEText:
    """Return a ``MIMEText`` with the standard headers set.

    Args:
        from_addr: ``From`` header value.
        to_addr: ``To`` header value (single recipient).
        subject: ``Subject`` header value.
        body: Plain-text message body (UTF-8).
        cc: Optional Cc recipients.  When non-empty the addresses are
            joined into a ``Cc`` header.
        in_reply_to: Optional ``In-Reply-To`` header value for threading.
        references: Optional ``References`` header value for threading.

    Returns:
        A ``MIMEText`` ready for ``send_message()``.
    """
# mime.py
def build_plain_text_message(...) -> MIMEText:
    msg = MIMEText(body, _charset="utf-8")
    msg["From"] = from_addr
    ...
    return msg

# smtp/__init__.py — send() becomes:
def send(self, ...) -> None:
    msg = build_plain_text_message(...)
    self._smtp.send_message(msg, from_addr=from_addr, to_addrs=to_addrs)
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if in_reply_to is not None:
        msg["In-Reply-To"] = in_reply_to
    if references is not None:
        msg["References"] = references
    return msg
