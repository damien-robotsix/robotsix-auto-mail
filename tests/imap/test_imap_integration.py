"""Integration tests for ImapClient against an in-process IMAP server."""

from __future__ import annotations

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap import ImapClient, MailboxInfo


@pytest.mark.integration
def test_list_folders_against_inprocess_server(imap_server):
    """ImapClient.list_folders() returns parsed mailboxes from the test server."""
    host, port = imap_server
    cfg = MailConfig(
        imap_host=host,
        imap_port=port,
        imap_tls_mode="none",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",  # pragma: allowlist secret
    )
    with ImapClient(cfg) as client:
        folders = client.list_folders()

    assert len(folders) == 2
    inbox, gmail = folders

    assert isinstance(inbox, MailboxInfo)
    assert inbox.name == "INBOX"
    assert inbox.delimiter == "/"
    assert "\\HasNoChildren" in inbox.attributes

    assert isinstance(gmail, MailboxInfo)
    assert gmail.name == "[Gmail]"
    assert gmail.delimiter == "/"
    assert "\\HasChildren" in gmail.attributes
    assert "\\Noselect" in gmail.attributes
