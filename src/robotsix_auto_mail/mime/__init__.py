"""Pure functions for constructing MIME email messages.

This module isolates MIME construction from SMTP transport so the
message builder can be tested without instantiating an SMTP client
and reused by any caller that needs to compose a plain-text email.
"""

from __future__ import annotations

from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import BinaryIO


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
    msg = MIMEText(body, _charset="utf-8")
    msg["From"] = from_addr
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


def build_multipart_message(
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    attachments: list[BinaryIO],
    attachment_names: list[str],
    *,
    cc: list[str] | None = None,
) -> MIMEMultipart:
    """Return a ``MIMEMultipart`` with a text body and file attachments.

    Args:
        from_addr: ``From`` header value.
        to_addr: ``To`` header value (single recipient).
        subject: ``Subject`` header value.
        body: Plain-text message body (UTF-8).
        attachments: List of open binary file-like objects.
        attachment_names: Corresponding filenames for each attachment.
        cc: Optional Cc recipients.

    Returns:
        A ``MIMEMultipart`` ready for ``send_message()`` or IMAP APPEND.
    """
    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    if cc:
        msg["Cc"] = ", ".join(cc)

    msg.attach(MIMEText(body, _charset="utf-8"))

    for file_obj, name in zip(attachments, attachment_names):
        part = MIMEBase("application", "octet-stream")
        part.set_payload(file_obj.read())
        part.add_header("Content-Disposition", "attachment", filename=name)
        msg.attach(part)

    return msg
