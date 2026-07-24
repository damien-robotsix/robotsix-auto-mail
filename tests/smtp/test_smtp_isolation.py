"""Tests that the SMTP client module does not depend on IMAP."""

from __future__ import annotations

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.smtp import SmtpClient

# ===================================================================
# Doesn't depend on IMAP
# ===================================================================


def test_smtp_client_imports_protocol_base_from_imap() -> None:
    """The smtp_client module imports _ProtocolClient from the imap package."""
    import robotsix_auto_mail.smtp as mod

    source = mod.__file__
    assert source is not None
    content = open(source).read()
    # The shared _ProtocolClient and build_xoauth2_response now live in the
    # imap package (formerly the standalone protocol module).
    assert (
        "from robotsix_auto_mail.imap import _ProtocolClient, build_xoauth2_response"
        in content
    )


def test_smtp_client_only_uses_smtp_fields(cfg: MailConfig) -> None:
    """SmtpClient constructor extracts only SMTP fields from MailConfig."""
    client = SmtpClient(cfg)
    assert client._host == "smtp.example.com"
    assert client._port == 587
    assert client._tls_mode == "starttls"
    assert client._username == "user@example.com"
    assert client._password == "s3cret"
    # IMAP fields are never stored
    assert not hasattr(client, "_imap_host")
