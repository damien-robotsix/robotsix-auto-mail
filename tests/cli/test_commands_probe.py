"""Direct unit tests for ``robotsix_auto_mail.cli.commands_probe``.

``tests/cli/test_cli_probe.py`` drives the probe command end-to-end through
``main()`` with mocked ``imaplib``/``smtplib``.  These tests target the
module's own functions directly: ``_cmd_probe`` (with ``ImapClient`` /
``SmtpClient`` / ``probe_account`` mocked at the module seam) and
``register_subparser``.
"""

from __future__ import annotations

import argparse

import pytest
from pydantic import SecretStr

from robotsix_auto_mail.cli import commands_probe
from robotsix_auto_mail.cli.commands_probe import _cmd_probe, register_subparser
from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap import ImapError
from robotsix_auto_mail.smtp import SmtpError


class _FakeFolder:
    def __init__(self, name: str, attributes: list[str], delimiter: str) -> None:
        self.name = name
        self.attributes = attributes
        self.delimiter = delimiter


class _FakeImap:
    """Context-manager stub mimicking the bits of ``ImapClient`` probe uses."""

    def __init__(
        self,
        *,
        greeting: bytes | None = b"* OK IMAP4 ready",
        capabilities: tuple[str, ...] = ("IMAP4rev1", "IDLE"),
        folders: list[_FakeFolder] | None = None,
    ) -> None:
        self.server_greeting = greeting
        self.capabilities = capabilities
        self._folders = (
            folders
            if folders is not None
            else [
                _FakeFolder("INBOX", ["\\HasNoChildren"], "/"),
            ]
        )

    def __enter__(self) -> "_FakeImap":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def list_folders(self) -> list[_FakeFolder]:
        return self._folders


class _FakeSmtp:
    """Context-manager stub mimicking the bits of ``SmtpClient`` probe uses."""

    def __init__(
        self,
        *,
        ehlo: bytes | None = b"250-smtp.example.com\n250 STARTTLS",
        features: dict[str, str] | None = None,
    ) -> None:
        self.ehlo_response = ehlo
        self.esmtp_features = (
            features
            if features is not None
            else {
                "STARTTLS": "",
                "AUTH": "PLAIN LOGIN",
            }
        )
        self.send_message_called = False

    def __enter__(self) -> "_FakeSmtp":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def send_message(self, *args: object, **kwargs: object) -> None:
        self.send_message_called = True


@pytest.fixture
def config() -> MailConfig:
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password=SecretStr("s3cret"),
    )


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    imap: object,
    smtp: object,
    status: str,
) -> None:
    monkeypatch.setattr(commands_probe, "ImapClient", imap)
    monkeypatch.setattr(commands_probe, "SmtpClient", smtp)
    monkeypatch.setattr(commands_probe, "probe_account", lambda cfg: (status, None))


# ---------------------------------------------------------------------------
# _cmd_probe — success
# ---------------------------------------------------------------------------


def test_cmd_probe_success(
    config: MailConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch(
        monkeypatch,
        imap=lambda cfg: _FakeImap(),
        smtp=lambda cfg: _FakeSmtp(),
        status="ok",
    )

    rc = _cmd_probe(config)

    assert rc == 0
    captured = capsys.readouterr()
    out, err = captured.out, captured.err
    assert "IMAP Probe" in out
    assert "* OK IMAP4 ready" in out
    assert "IMAP4rev1" in out
    assert "INBOX" in out
    assert "SMTP Probe" in out
    assert "250-smtp.example.com" in out
    assert "STARTTLS" in out
    assert err == ""


def test_cmd_probe_missing_greeting_and_ehlo(
    config: MailConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch(
        monkeypatch,
        imap=lambda cfg: _FakeImap(greeting=None, folders=[]),
        smtp=lambda cfg: _FakeSmtp(ehlo=None, features={}),
        status="ok",
    )

    rc = _cmd_probe(config)

    assert rc == 0
    out = capsys.readouterr().out
    assert "Greeting: (none)" in out
    assert "(no folders returned)" in out
    assert "EHLO response: (none)" in out
    assert "(no features)" in out


# ---------------------------------------------------------------------------
# _cmd_probe — failures
# ---------------------------------------------------------------------------


def test_cmd_probe_imap_error_still_probes_smtp(
    config: MailConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _raise_imap(cfg: MailConfig) -> _FakeImap:
        raise ImapError("AUTHENTICATIONFAILED")

    _patch(
        monkeypatch,
        imap=_raise_imap,
        smtp=lambda cfg: _FakeSmtp(),
        status="failed",
    )

    rc = _cmd_probe(config)

    assert rc == 1
    captured = capsys.readouterr()
    out, err = captured.out, captured.err
    assert "SMTP Probe" in out
    assert "250-smtp.example.com" in out
    assert "Error: AUTHENTICATIONFAILED" in err


def test_cmd_probe_smtp_error_still_probes_imap(
    config: MailConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _raise_smtp(cfg: MailConfig) -> _FakeSmtp:
        raise SmtpError("connection refused")

    _patch(
        monkeypatch,
        imap=lambda cfg: _FakeImap(),
        smtp=_raise_smtp,
        status="failed",
    )

    rc = _cmd_probe(config)

    assert rc == 1
    captured = capsys.readouterr()
    out, err = captured.out, captured.err
    assert "INBOX" in out
    assert "Error: connection refused" in err


def test_cmd_probe_returns_one_when_probe_account_fails(
    config: MailConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Even when diagnostics render, a failed probe_account verdict is rc 1."""
    _patch(
        monkeypatch,
        imap=lambda cfg: _FakeImap(),
        smtp=lambda cfg: _FakeSmtp(),
        status="failed",
    )

    rc = _cmd_probe(config)

    assert rc == 1
    capsys.readouterr()


def test_cmd_probe_never_sends_message(
    config: MailConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_smtp = _FakeSmtp()
    _patch(
        monkeypatch,
        imap=lambda cfg: _FakeImap(),
        smtp=lambda cfg: fake_smtp,
        status="ok",
    )

    _cmd_probe(config)
    capsys.readouterr()

    assert fake_smtp.send_message_called is False


# ---------------------------------------------------------------------------
# register_subparser
# ---------------------------------------------------------------------------


def test_register_subparser_adds_probe_command() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    register_subparser(subparsers)

    args = parser.parse_args(["probe", "--account", "acct1"])
    assert args.command == "probe"
    assert args.account == "acct1"
