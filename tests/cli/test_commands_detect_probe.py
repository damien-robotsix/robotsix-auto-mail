"""Unit tests for ``robotsix_auto_mail.cli.commands_detect`` — capability probing.

Tests _probe_capabilities with mock ImapClient / SmtpClient.
"""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.cli.commands_detect import _probe_capabilities
from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap.errors import ImapError
from robotsix_auto_mail.smtp import SmtpError

# ---------------------------------------------------------------------------
# _probe_capabilities — mock ImapClient / SmtpClient
# ---------------------------------------------------------------------------


class _FakeImap:
    """Fake IMAP client whose capabilities attribute is a canned iterable."""

    def __init__(self, caps: list[str]) -> None:
        self.capabilities = caps

    def __enter__(self) -> "_FakeImap":
        return self

    def __exit__(self, *args: object) -> None:
        pass


class _FakeSmtp:
    """Fake SMTP client whose esmtp_features attribute is a canned dict."""

    def __init__(self, features: dict[str, str]) -> None:
        self.esmtp_features = features

    def __enter__(self) -> "_FakeSmtp":
        return self

    def __exit__(self, *args: object) -> None:
        pass


def test_probe_capabilities_not_verified() -> None:
    """When verified=False, returns empty collections without opening connections."""
    config = MailConfig(
        imap_host="h",
        smtp_host="s",
        username="u",
        password="p",
    )
    with (
        mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_imap,
        mock.patch("robotsix_auto_mail.smtp.SmtpClient") as mock_smtp,
    ):
        imap_caps, smtp_feats = _probe_capabilities(config, verified=False)

    assert imap_caps == []
    assert smtp_feats == {}
    mock_imap.assert_not_called()
    mock_smtp.assert_not_called()


def test_probe_capabilities_success() -> None:
    """Successfully collects IMAP capabilities and SMTP features."""
    config = MailConfig(
        imap_host="h",
        smtp_host="s",
        username="u",
        password="p",
    )
    with (
        mock.patch(
            "robotsix_auto_mail.imap.ImapClient",
            return_value=_FakeImap(["IMAP4rev1", "IDLE", "MOVE"]),
        ),
        mock.patch(
            "robotsix_auto_mail.smtp.SmtpClient",
            return_value=_FakeSmtp({"SIZE": "10240000", "STARTTLS": ""}),
        ),
    ):
        imap_caps, smtp_feats = _probe_capabilities(config, verified=True)

    assert imap_caps == ["IMAP4rev1", "IDLE", "MOVE"]
    assert smtp_feats == {"SIZE": "10240000", "STARTTLS": ""}


def test_probe_capabilities_imap_oserror() -> None:
    """IMAP OSError is caught; SMTP still probed."""
    config = MailConfig(
        imap_host="h",
        smtp_host="s",
        username="u",
        password="p",
    )
    with (
        mock.patch(
            "robotsix_auto_mail.imap.ImapClient",
            side_effect=OSError("connection refused"),
        ),
        mock.patch(
            "robotsix_auto_mail.smtp.SmtpClient",
            return_value=_FakeSmtp({"STARTTLS": ""}),
        ),
    ):
        imap_caps, smtp_feats = _probe_capabilities(config, verified=True)

    assert imap_caps == []
    assert smtp_feats == {"STARTTLS": ""}


def test_probe_capabilities_imap_imap_error() -> None:
    """ImapError is caught; SMTP still probed."""
    config = MailConfig(
        imap_host="h",
        smtp_host="s",
        username="u",
        password="p",
    )
    with (
        mock.patch(
            "robotsix_auto_mail.imap.ImapClient",
            side_effect=ImapError("bad"),
        ),
        mock.patch(
            "robotsix_auto_mail.smtp.SmtpClient",
            return_value=_FakeSmtp({"AUTH": "PLAIN"}),
        ),
    ):
        imap_caps, smtp_feats = _probe_capabilities(config, verified=True)

    assert imap_caps == []
    assert smtp_feats == {"AUTH": "PLAIN"}


def test_probe_capabilities_smtp_oserror() -> None:
    """SMTP OSError is caught; IMAP result is preserved."""
    config = MailConfig(
        imap_host="h",
        smtp_host="s",
        username="u",
        password="p",
    )
    with (
        mock.patch(
            "robotsix_auto_mail.imap.ImapClient",
            return_value=_FakeImap(["IMAP4rev1"]),
        ),
        mock.patch(
            "robotsix_auto_mail.smtp.SmtpClient",
            side_effect=OSError("timeout"),
        ),
    ):
        imap_caps, smtp_feats = _probe_capabilities(config, verified=True)

    assert imap_caps == ["IMAP4rev1"]
    assert smtp_feats == {}


def test_probe_capabilities_smtp_error() -> None:
    """SmtpError is caught; IMAP result is preserved."""
    config = MailConfig(
        imap_host="h",
        smtp_host="s",
        username="u",
        password="p",
    )
    with (
        mock.patch(
            "robotsix_auto_mail.imap.ImapClient",
            return_value=_FakeImap(["IMAP4rev1"]),
        ),
        mock.patch(
            "robotsix_auto_mail.smtp.SmtpClient",
            side_effect=SmtpError("bad"),
        ),
    ):
        imap_caps, smtp_feats = _probe_capabilities(config, verified=True)

    assert imap_caps == ["IMAP4rev1"]
    assert smtp_feats == {}


def test_probe_capabilities_both_fail() -> None:
    """Both IMAP and SMTP fail — returns empty collections."""
    config = MailConfig(
        imap_host="h",
        smtp_host="s",
        username="u",
        password="p",
    )
    with (
        mock.patch(
            "robotsix_auto_mail.imap.ImapClient",
            side_effect=OSError("nope"),
        ),
        mock.patch(
            "robotsix_auto_mail.smtp.SmtpClient",
            side_effect=SmtpError("also nope"),
        ),
    ):
        imap_caps, smtp_feats = _probe_capabilities(config, verified=True)

    assert imap_caps == []
    assert smtp_feats == {}
