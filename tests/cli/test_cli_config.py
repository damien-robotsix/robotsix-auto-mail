"""Tests for cli/config.py — verify/refine/detect helpers."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.cli.config import (
    _account_id_from_email,
    _existing_account_ids,
    _existing_accounts_for_append,
    _get_password,
    _report_verify_result,
    _verify_config,
    _verify_feedback,
    _VerifyResult,
)
from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap import ImapAuthError, ImapClient, ImapError
from robotsix_auto_mail.smtp import SmtpAuthError, SmtpClient, SmtpError

# ---------------------------------------------------------------------------
# _VerifyResult property tests — all 9 combinatorial outcomes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    (
        "imap_ok",
        "smtp_ok",
        "imap_auth",
        "smtp_auth",
        "expect_ok",
        "expect_host",
        "expect_auth_only",
    ),
    [
        # Both ok
        (True, True, False, False, True, False, False),
        # IMAP ok, SMTP auth fail
        (True, False, False, True, False, False, True),
        # IMAP ok, SMTP connection fail
        (True, False, False, False, False, True, False),
        # SMTP ok, IMAP auth fail
        (False, True, True, False, False, False, True),
        # SMTP ok, IMAP connection fail
        (False, True, False, False, False, True, False),
        # Both auth fail
        (False, False, True, True, False, False, True),
        # Both connection fail
        (False, False, False, False, False, True, False),
        # IMAP auth, SMTP connection
        (False, False, True, False, False, True, False),
        # IMAP connection, SMTP auth
        (False, False, False, True, False, True, False),
    ],
)
def test_verify_result_properties(
    imap_ok: bool,
    smtp_ok: bool,
    imap_auth: bool,
    smtp_auth: bool,
    expect_ok: bool,
    expect_host: bool,
    expect_auth_only: bool,
) -> None:
    """_VerifyResult.ok / .host_problem / .only_auth_problem cover all combinations."""
    result = _VerifyResult(
        imap_ok=imap_ok,
        smtp_ok=smtp_ok,
        imap_auth=imap_auth,
        smtp_auth=smtp_auth,
    )
    assert result.ok == expect_ok
    assert result.host_problem == expect_host
    assert result.only_auth_problem == expect_auth_only


def test_verify_result_defaults() -> None:
    """Default-constructed _VerifyResult has all properties False."""
    result = _VerifyResult(imap_ok=False, smtp_ok=False)
    assert result.ok is False
    assert result.host_problem is True  # both not ok, neither auth
    assert result.only_auth_problem is False
    assert result.imap_error == ""
    assert result.smtp_error == ""


# ---------------------------------------------------------------------------
# _account_id_from_email
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("user@gmail.com", "user-gmail.com"),
        ("john.doe@example.co.uk", "john.doe-example.co.uk"),
        ("localpart", "localpart"),
        ("UPPER@CASE.COM", "UPPER-CASE.COM"),
        ("a@b", "a-b"),
        ("user+tag@domain.com", "user-tag-domain.com"),
        ("spaces in@domain.com", "spaces-in-domain.com"),
        ("@domain.com", "domain.com"),
        ("!!!@!!!", "default"),
        ("", "default"),
    ],
)
def test_account_id_from_email(email: str, expected: str) -> None:
    """_account_id_from_email derives safe filesystem ids from email addresses."""
    assert _account_id_from_email(email) == expected


# ---------------------------------------------------------------------------
# _existing_account_ids
# ---------------------------------------------------------------------------


def test_existing_account_ids_missing_file(tmp_path: Path) -> None:
    """A non-existent path returns an empty set."""
    path = tmp_path / "nonexistent.json"
    assert _existing_account_ids(path) == set()


def test_existing_account_ids_multi_account(tmp_path: Path) -> None:
    """A multi-account JSON file returns its entry ids."""
    import json

    path = tmp_path / "accounts.json"
    path.write_text(
        json.dumps(
            {
                "accounts": [
                    {"account_id": "alpha"},
                    {"account_id": "beta"},
                ],
                "default_account_id": "alpha",
            }
        )
    )
    assert _existing_account_ids(path) == {"alpha", "beta"}


def test_existing_account_ids_empty_json(tmp_path: Path) -> None:
    """An empty JSON file returns an empty set."""
    path = tmp_path / "empty.json"
    path.write_text("")
    assert _existing_account_ids(path) == set()


def test_existing_account_ids_invalid_json(tmp_path: Path) -> None:
    """A corrupt JSON file returns an empty set (graceful degradation)."""
    path = tmp_path / "corrupt.json"
    path.write_text("{invalid: [[[")
    assert _existing_account_ids(path) == set()


def test_existing_account_ids_accounts_not_list(tmp_path: Path) -> None:
    """An 'accounts' key that is not a list → empty set."""
    import json

    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"accounts": "not-a-list"}))
    assert _existing_account_ids(path) == set()


def test_existing_account_ids_entry_missing_id(tmp_path: Path) -> None:
    """Account entries without an 'account_id' field are skipped."""
    import json

    path = tmp_path / "partial.json"
    path.write_text(
        json.dumps(
            {
                "accounts": [
                    {"account_id": "ok"},
                    {"email": "b@b.com"},  # no account_id
                ],
                "default_account_id": "ok",
            }
        )
    )
    assert _existing_account_ids(path) == {"ok"}


# ---------------------------------------------------------------------------
# _existing_accounts_for_append
# ---------------------------------------------------------------------------


def test_existing_accounts_for_append_missing_file(tmp_path: Path) -> None:
    """A non-existent path returns empty list and the new id as default."""
    path = tmp_path / "nonexistent.json"
    others, default_id = _existing_accounts_for_append(path, "new-id")
    assert others == []
    assert default_id == "new-id"


def test_existing_accounts_for_append_multi_account(tmp_path: Path) -> None:
    """Multi-account file: existing accounts returned, matching id excluded."""
    import json

    path = tmp_path / "multi.json"
    path.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "account_id": "alpha",
                        "config": {
                            "imap_host": "imap.a.com",
                            "smtp_host": "smtp.a.com",
                            "username": "a@a.com",
                            "password": "",
                        },
                    },
                    {
                        "account_id": "beta",
                        "config": {
                            "imap_host": "imap.b.com",
                            "smtp_host": "smtp.b.com",
                            "username": "b@b.com",
                            "password": "",
                        },
                    },
                ],
                "default_account_id": "alpha",
            }
        )
    )
    others, default_id = _existing_accounts_for_append(path, "beta")
    assert default_id == "alpha"
    assert len(others) == 1
    assert others[0].account_id == "alpha"
    assert others[0].config.username == "a@a.com"


def test_existing_accounts_for_append_new_id(tmp_path: Path) -> None:
    """When the new id is not in the file, all accounts are returned as others."""
    import json

    path = tmp_path / "multi.json"
    path.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "account_id": "alpha",
                        "config": {
                            "imap_host": "imap.a.com",
                            "smtp_host": "smtp.a.com",
                            "username": "a@a.com",
                            "password": "",
                        },
                    }
                ],
                "default_account_id": "alpha",
            }
        )
    )
    others, default_id = _existing_accounts_for_append(path, "gamma")
    assert default_id == "alpha"
    assert len(others) == 1
    assert others[0].account_id == "alpha"


def test_existing_accounts_for_append_non_accounts_file(tmp_path: Path) -> None:
    """A file without an `accounts:` list is not valid config → start fresh."""
    import json

    path = tmp_path / "mono.json"
    path.write_text(
        json.dumps(
            {
                "imap_host": "imap.old.com",
                "smtp_host": "smtp.old.com",
                "username": "old@example.com",
                "password": "",
            }
        )
    )
    others, default_id = _existing_accounts_for_append(path, "new-id")
    assert others == []
    assert default_id == "new-id"


def test_existing_accounts_for_append_invalid_json(tmp_path: Path) -> None:
    """Corrupt JSON → graceful fallback."""
    path = tmp_path / "corrupt.json"
    path.write_text("{invalid:")
    others, default_id = _existing_accounts_for_append(path, "myid")
    assert others == []
    assert default_id == "myid"


# ---------------------------------------------------------------------------
# _verify_config
# ---------------------------------------------------------------------------


def test_verify_config_success(cfg: MailConfig) -> None:
    """Both IMAP and SMTP connections succeed → ok=True, no errors."""
    mock_imap = mock.MagicMock(spec=ImapClient)
    mock_imap.__enter__.return_value = mock_imap  # context manager returns self
    mock_smtp = mock.MagicMock(spec=SmtpClient)
    mock_smtp.__enter__.return_value = mock_smtp

    with (
        mock.patch("robotsix_auto_mail.cli.config.ImapClient", return_value=mock_imap),
        mock.patch("robotsix_auto_mail.cli.config.SmtpClient", return_value=mock_smtp),
    ):
        result = _verify_config(cfg)

    assert result.ok is True
    assert result.host_problem is False
    assert result.only_auth_problem is False
    mock_imap.list_folders.assert_called_once()
    mock_smtp.__enter__.assert_called_once()


def test_verify_config_imap_auth_failure(cfg: MailConfig) -> None:
    """ImapAuthError → imap_auth=True, imap_ok=False."""
    mock_imap = mock.MagicMock(spec=ImapClient)
    mock_imap.__enter__.return_value = mock_imap
    mock_imap.list_folders.side_effect = ImapAuthError("bad password")
    mock_smtp = mock.MagicMock(spec=SmtpClient)
    mock_smtp.__enter__.return_value = mock_smtp

    with (
        mock.patch("robotsix_auto_mail.cli.config.ImapClient", return_value=mock_imap),
        mock.patch("robotsix_auto_mail.cli.config.SmtpClient", return_value=mock_smtp),
    ):
        result = _verify_config(cfg)

    assert result.imap_ok is False
    assert result.imap_auth is True
    assert "bad password" in result.imap_error
    assert result.smtp_ok is True
    assert result.ok is False
    assert result.only_auth_problem is True


def test_verify_config_smtp_auth_failure(cfg: MailConfig) -> None:
    """SmtpAuthError → smtp_auth=True, smtp_ok=False."""
    mock_imap = mock.MagicMock(spec=ImapClient)
    mock_imap.__enter__.return_value = mock_imap
    mock_smtp = mock.MagicMock(spec=SmtpClient)
    mock_smtp.__enter__.return_value = mock_smtp
    mock_smtp.__enter__.side_effect = SmtpAuthError("auth failed")

    with (
        mock.patch("robotsix_auto_mail.cli.config.ImapClient", return_value=mock_imap),
        mock.patch("robotsix_auto_mail.cli.config.SmtpClient", return_value=mock_smtp),
    ):
        result = _verify_config(cfg)

    assert result.smtp_ok is False
    assert result.smtp_auth is True
    assert "auth failed" in result.smtp_error
    assert result.imap_ok is True
    assert result.only_auth_problem is True


def test_verify_config_imap_connection_failure(cfg: MailConfig) -> None:
    """ImapError (non-auth) → imap_ok=False, imap_auth=False."""
    mock_imap = mock.MagicMock(spec=ImapClient)
    mock_imap.__enter__.return_value = mock_imap
    mock_imap.list_folders.side_effect = ImapError("connection refused")
    mock_smtp = mock.MagicMock(spec=SmtpClient)
    mock_smtp.__enter__.return_value = mock_smtp

    with (
        mock.patch("robotsix_auto_mail.cli.config.ImapClient", return_value=mock_imap),
        mock.patch("robotsix_auto_mail.cli.config.SmtpClient", return_value=mock_smtp),
    ):
        result = _verify_config(cfg)

    assert result.imap_ok is False
    assert result.imap_auth is False
    assert "connection refused" in result.imap_error
    assert result.smtp_ok is True
    assert result.host_problem is True


def test_verify_config_smtp_connection_failure(cfg: MailConfig) -> None:
    """SmtpError (non-auth) → smtp_ok=False, smtp_auth=False."""
    mock_imap = mock.MagicMock(spec=ImapClient)
    mock_imap.__enter__.return_value = mock_imap
    mock_smtp = mock.MagicMock(spec=SmtpClient)
    mock_smtp.__enter__.return_value = mock_smtp
    mock_smtp.__enter__.side_effect = SmtpError("timeout")

    with (
        mock.patch("robotsix_auto_mail.cli.config.ImapClient", return_value=mock_imap),
        mock.patch("robotsix_auto_mail.cli.config.SmtpClient", return_value=mock_smtp),
    ):
        result = _verify_config(cfg)

    assert result.smtp_ok is False
    assert result.smtp_auth is False
    assert "timeout" in result.smtp_error
    assert result.imap_ok is True
    assert result.host_problem is True


def test_verify_config_both_fail_auth(cfg: MailConfig) -> None:
    """Both IMAP and SMTP auth failures → only_auth_problem=True."""
    mock_imap = mock.MagicMock(spec=ImapClient)
    mock_imap.__enter__.return_value = mock_imap
    mock_imap.list_folders.side_effect = ImapAuthError("imap auth bad")
    mock_smtp = mock.MagicMock(spec=SmtpClient)
    mock_smtp.__enter__.return_value = mock_smtp
    mock_smtp.__enter__.side_effect = SmtpAuthError("smtp auth bad")

    with (
        mock.patch("robotsix_auto_mail.cli.config.ImapClient", return_value=mock_imap),
        mock.patch("robotsix_auto_mail.cli.config.SmtpClient", return_value=mock_smtp),
    ):
        result = _verify_config(cfg)

    assert result.ok is False
    assert result.host_problem is False
    assert result.only_auth_problem is True
    assert result.imap_auth is True
    assert result.smtp_auth is True


def test_verify_config_both_fail_connection(cfg: MailConfig) -> None:
    """Both IMAP and SMTP connection failures → host_problem=True."""
    mock_imap = mock.MagicMock(spec=ImapClient)
    mock_imap.__enter__.return_value = mock_imap
    mock_imap.list_folders.side_effect = ImapError("imap down")
    mock_smtp = mock.MagicMock(spec=SmtpClient)
    mock_smtp.__enter__.return_value = mock_smtp
    mock_smtp.__enter__.side_effect = SmtpError("smtp down")

    with (
        mock.patch("robotsix_auto_mail.cli.config.ImapClient", return_value=mock_imap),
        mock.patch("robotsix_auto_mail.cli.config.SmtpClient", return_value=mock_smtp),
    ):
        result = _verify_config(cfg)

    assert result.ok is False
    assert result.host_problem is True
    assert result.only_auth_problem is False
    assert result.imap_auth is False
    assert result.smtp_auth is False


# ---------------------------------------------------------------------------
# _report_verify_result
# ---------------------------------------------------------------------------


def test_report_verify_result_all_ok(capsys: pytest.CaptureFixture[str]) -> None:
    """Both ok → prints 'ok' for each."""
    result = _VerifyResult(imap_ok=True, smtp_ok=True)
    _report_verify_result(result)
    captured = capsys.readouterr()
    assert "IMAP: ok" in captured.err
    assert "SMTP: ok" in captured.err


def test_report_verify_result_auth_fail(capsys: pytest.CaptureFixture[str]) -> None:
    """Auth failures are labelled 'auth failed'."""
    result = _VerifyResult(
        imap_ok=False,
        smtp_ok=False,
        imap_auth=True,
        smtp_auth=True,
        imap_error="bad creds",
        smtp_error="bad creds",
    )
    _report_verify_result(result)
    captured = capsys.readouterr()
    assert "IMAP: auth failed — bad creds" in captured.err
    assert "SMTP: auth failed — bad creds" in captured.err


def test_report_verify_result_connection_fail(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-auth failures are labelled 'connection failed'."""
    result = _VerifyResult(
        imap_ok=False,
        smtp_ok=True,
        imap_error="refused",
    )
    _report_verify_result(result)
    captured = capsys.readouterr()
    assert "IMAP: connection failed — refused" in captured.err
    assert "SMTP: ok" in captured.err


# ---------------------------------------------------------------------------
# _verify_feedback
# ---------------------------------------------------------------------------


def test_verify_feedback_host_problems(cfg: MailConfig) -> None:
    """Non-auth failures produce host-targeted feedback strings."""
    result = _VerifyResult(
        imap_ok=False,
        smtp_ok=False,
        imap_error="no route",
        smtp_error="timeout",
    )
    msg = _verify_feedback(cfg, result)
    assert "IMAP host" in msg
    assert str(cfg.imap_host) in msg
    assert "no route" in msg
    assert "SMTP host" in msg
    assert str(cfg.smtp_host) in msg
    assert "timeout" in msg


def test_verify_feedback_no_host_problems(cfg: MailConfig) -> None:
    """Auth-only problems produce empty feedback (no host-level issues)."""
    result = _VerifyResult(
        imap_ok=False,
        smtp_ok=True,
        imap_auth=True,
        imap_error="auth",
    )
    msg = _verify_feedback(cfg, result)
    assert msg == ""  # imap auth failure only → not a host problem


def test_verify_feedback_mixed(cfg: MailConfig) -> None:
    """Only the non-auth failures appear in feedback."""
    result = _VerifyResult(
        imap_ok=False,
        smtp_ok=False,
        imap_auth=False,
        smtp_auth=True,
        imap_error="refused",
        smtp_error="bad password",
    )
    msg = _verify_feedback(cfg, result)
    assert "IMAP host" in msg
    assert "SMTP host" not in msg  # SMTP was auth, not host


# ---------------------------------------------------------------------------
# _get_password
# ---------------------------------------------------------------------------


def test_get_password_from_args() -> None:
    """--password on the command line is used directly."""
    args = argparse.Namespace(password="cli-pass", stdout=False)
    assert _get_password(args) == "cli-pass"


def test_get_password_interactive() -> None:
    """No --password, not stdout: prompts via getpass."""
    args = argparse.Namespace(password=None, stdout=False)
    with mock.patch("getpass.getpass", return_value="typed-pass"):
        assert _get_password(args) == "typed-pass"


def test_get_password_stdout_mode() -> None:
    """stdout mode returns empty string (no prompt)."""
    args = argparse.Namespace(password=None, stdout=True)
    assert _get_password(args) == ""


def test_get_password_eof() -> None:
    """EOFError during prompt → None returned, message printed."""
    args = argparse.Namespace(password=None, stdout=False)
    with mock.patch("getpass.getpass", side_effect=EOFError):
        result = _get_password(args)
    assert result is None


def test_get_password_keyboard_interrupt() -> None:
    """KeyboardInterrupt during prompt → None returned."""
    args = argparse.Namespace(password=None, stdout=False)
    with mock.patch("getpass.getpass", side_effect=KeyboardInterrupt):
        result = _get_password(args)
    assert result is None


# ---------------------------------------------------------------------------
# _existing_accounts_for_append — validation-failure edge cases
# ---------------------------------------------------------------------------


def test_existing_accounts_for_append_multi_account_validation_error(
    tmp_path: Path,
) -> None:
    """Multi-account JSON that parses but fails schema validation → graceful fallback."""
    import json

    path = tmp_path / "bad_schema.json"
    path.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "account_id": "ok",
                        "config": {
                            "imap_host": "imap.ok.com",
                            "smtp_host": "smtp.ok.com",
                            "username": "a@a.com",
                            "password": "",
                        },
                    }
                ],
                "default_account_id": "ok",
            }
        )
    )
    # Force MailAccountsConfig.model_validate to raise to cover the except path.
    with mock.patch(
        "robotsix_auto_mail.cli.config.MailAccountsConfig.model_validate",
        side_effect=ValueError("schema mismatch"),
    ):
        others, default_id = _existing_accounts_for_append(path, "new-id")
    assert others == []
    assert default_id == "new-id"
