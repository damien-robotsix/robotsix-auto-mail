"""Interactive password refinement tests (_refine_password)."""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.cli.config import _refine_password
from robotsix_auto_mail.config.detect import MailProvider
from tests.cli.conftest import _build_config


def test_refine_password_stops_on_cancel() -> None:
    """_refine_password signals stop when the prompt is cancelled."""
    from robotsix_auto_mail.cli import _refine_password

    provider = MailProvider(imap_host="imap.x.net", smtp_host="smtp.x.net")
    build = mock.MagicMock()

    with mock.patch("getpass.getpass", side_effect=KeyboardInterrupt):
        outcome = _refine_password(build, provider)

    assert outcome.config is None
    build.assert_not_called()


def test_refine_password_returns_new_config(capsys: pytest.CaptureFixture[str]) -> None:
    """Successful interactive re-entry → new config with updated password."""
    provider = MailProvider(imap_host="imap.test.com", smtp_host="smtp.test.com")
    with mock.patch("getpass.getpass", return_value="newpass"):
        outcome = _refine_password(_build_config, provider)
    assert outcome.config is not None
    assert (
        outcome.config.password.get_secret_value() == "newpass"
    )  # pragma: allowlist secret
    assert outcome.config.imap_host == "imap.test.com"
    captured = capsys.readouterr()
    assert "password was rejected" in captured.err


def test_refine_password_empty_input(capsys: pytest.CaptureFixture[str]) -> None:
    """Empty password input → no config returned."""
    provider = MailProvider(imap_host="imap.test.com", smtp_host="smtp.test.com")
    with mock.patch("getpass.getpass", return_value=""):
        outcome = _refine_password(_build_config, provider)
    assert outcome.config is None
    assert outcome.provider is None


def test_refine_password_eof() -> None:
    """EOF during re-entry → no config."""
    provider = MailProvider(imap_host="imap.test.com", smtp_host="smtp.test.com")
    with mock.patch("getpass.getpass", side_effect=EOFError):
        outcome = _refine_password(_build_config, provider)
    assert outcome.config is None
