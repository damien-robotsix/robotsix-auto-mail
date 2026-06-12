"""Tests for cli/config.py — verify/refine/detect helpers."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from robotsix_auto_mail.cli.config import (
    _account_id_from_email,
    _detect_settings,
    _existing_account_ids,
    _existing_accounts_for_append,
    _get_password,
    _refine_manual,
    _refine_password,
    _refine_with_llm,
    _report_failure,
    _report_verify_result,
    _verify_and_refine,
    _verify_config,
    _verify_feedback,
    _VerifyResult,
)
from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.detect import DetectionError, MailProvider
from robotsix_auto_mail.imap import ImapAuthError, ImapClient, ImapError
from robotsix_auto_mail.smtp import SmtpAuthError, SmtpClient, SmtpError

# ---------------------------------------------------------------------------
# _VerifyResult property tests — all 9 combinatorial outcomes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "imap_ok, smtp_ok, imap_auth, smtp_auth, expect_ok, expect_host, expect_auth_only",
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
    "email, expected",
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
    path = tmp_path / "nonexistent.yaml"
    assert _existing_account_ids(path) == set()


def test_existing_account_ids_multi_account(tmp_path: Path) -> None:
    """A multi-account YAML file returns its entry ids."""
    path = tmp_path / "accounts.yaml"
    path.write_text(
        "accounts:\n"
        "  - id: alpha\n"
        "    email: a@a.com\n"
        "  - id: beta\n"
        "    email: b@b.com\n"
        "default_account_id: alpha\n"
    )
    assert _existing_account_ids(path) == {"alpha", "beta"}


def test_existing_account_ids_mono_config(tmp_path: Path) -> None:
    """A deprecated mono config file returns {'default'}."""
    path = tmp_path / "mono.yaml"
    path.write_text(
        "auth:\n  username: user@example.com\nimap:\n  host: imap.example.com\n"
    )
    assert _existing_account_ids(path) == {"default"}


def test_existing_account_ids_empty_yaml(tmp_path: Path) -> None:
    """An empty YAML file returns an empty set."""
    path = tmp_path / "empty.yaml"
    path.write_text("")
    assert _existing_account_ids(path) == set()


def test_existing_account_ids_invalid_yaml(tmp_path: Path) -> None:
    """A corrupt YAML file returns an empty set (graceful degradation)."""
    path = tmp_path / "corrupt.yaml"
    path.write_text("{invalid: [[[")
    assert _existing_account_ids(path) == set()


def test_existing_account_ids_accounts_not_list(tmp_path: Path) -> None:
    """An 'accounts' key that is not a list falls through to mono detection."""
    path = tmp_path / "bad.yaml"
    path.write_text("accounts: not-a-list\n")
    # "accounts" is not a list, but data is a non-empty dict → mono → {"default"}
    assert _existing_account_ids(path) == {"default"}


def test_existing_account_ids_entry_missing_id(tmp_path: Path) -> None:
    """Account entries without an 'id' field are skipped."""
    path = tmp_path / "partial.yaml"
    path.write_text(
        "accounts:\n"
        "  - id: ok\n"
        "    email: a@a.com\n"
        "  - email: b@b.com\n"  # no id
        "default_account_id: ok\n"
    )
    assert _existing_account_ids(path) == {"ok"}


# ---------------------------------------------------------------------------
# _existing_accounts_for_append
# ---------------------------------------------------------------------------


def test_existing_accounts_for_append_missing_file(tmp_path: Path) -> None:
    """A non-existent path returns empty list and the new id as default."""
    path = tmp_path / "nonexistent.yaml"
    others, default_id = _existing_accounts_for_append(path, "new-id")
    assert others == []
    assert default_id == "new-id"


def test_existing_accounts_for_append_multi_account(tmp_path: Path) -> None:
    """Multi-account file: existing accounts returned, matching id excluded."""
    path = tmp_path / "multi.yaml"
    path.write_text(
        "accounts:\n"
        "  - id: alpha\n"
        "    auth:\n"
        "      username: a@a.com\n"
        "    imap:\n"
        "      host: imap.a.com\n"
        "    smtp:\n"
        "      host: smtp.a.com\n"
        "  - id: beta\n"
        "    auth:\n"
        "      username: b@b.com\n"
        "    imap:\n"
        "      host: imap.b.com\n"
        "    smtp:\n"
        "      host: smtp.b.com\n"
        "default_account_id: alpha\n"
    )
    others, default_id = _existing_accounts_for_append(path, "beta")
    assert default_id == "alpha"
    assert len(others) == 1
    assert others[0].account_id == "alpha"
    assert others[0].config.username == "a@a.com"


def test_existing_accounts_for_append_new_id(tmp_path: Path) -> None:
    """When the new id is not in the file, all accounts are returned as others."""
    path = tmp_path / "multi.yaml"
    path.write_text(
        "accounts:\n"
        "  - id: alpha\n"
        "    auth:\n"
        "      username: a@a.com\n"
        "    imap:\n"
        "      host: imap.a.com\n"
        "    smtp:\n"
        "      host: smtp.a.com\n"
        "default_account_id: alpha\n"
    )
    others, default_id = _existing_accounts_for_append(path, "gamma")
    assert default_id == "alpha"
    assert len(others) == 1
    assert others[0].account_id == "alpha"


def test_existing_accounts_for_append_mono_file_default(tmp_path: Path) -> None:
    """Mono file with new id 'default': no 'other' accounts needed."""
    path = tmp_path / "mono.yaml"
    path.write_text(
        "auth:\n"
        "  username: old@example.com\n"
        "imap:\n"
        "  host: imap.old.com\n"
        "smtp:\n"
        "  host: smtp.old.com\n"
    )
    others, default_id = _existing_accounts_for_append(path, "default")
    assert default_id == "default"
    assert others == []


def test_existing_accounts_for_append_mono_file_other_id(tmp_path: Path) -> None:
    """Mono file with a different new id: old becomes 'default' in others."""
    path = tmp_path / "mono.yaml"
    path.write_text(
        "auth:\n"
        "  username: old@example.com\n"
        "imap:\n"
        "  host: imap.old.com\n"
        "smtp:\n"
        "  host: smtp.old.com\n"
    )
    others, default_id = _existing_accounts_for_append(path, "new-id")
    assert default_id == "default"
    assert len(others) == 1
    assert others[0].account_id == "default"
    assert others[0].config.username == "old@example.com"


def test_existing_accounts_for_append_invalid_yaml(tmp_path: Path) -> None:
    """Corrupt YAML → graceful fallback."""
    path = tmp_path / "corrupt.yaml"
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
# _detect_settings
# ---------------------------------------------------------------------------


def _mock_autoconfig(email: str) -> MailProvider | None:
    return MailProvider(
        imap_host="imap.autoconfig.com", smtp_host="smtp.autoconfig.com"
    )


def _mock_autoconfig_none(email: str) -> MailProvider | None:
    return None


def _mock_mx(email: str) -> list[str]:
    return ["mx1.example.com", "mx2.example.com"]


def _mock_mx_empty(email: str) -> list[str]:
    return []


def _mock_provider_from_mx(mx_hosts: list[str]) -> MailProvider | None:
    if mx_hosts:
        return MailProvider(imap_host="imap.mx.com", smtp_host="smtp.mx.com")
    return None


def _mock_detect(email: str, **kwargs: Any) -> MailProvider:
    return MailProvider(imap_host="imap.llm.com", smtp_host="smtp.llm.com")


def _mock_detect_error(email: str, **kwargs: Any) -> MailProvider:
    raise DetectionError("LLM unavailable")


def test_detect_settings_autoconfig_hit(capsys: pytest.CaptureFixture[str]) -> None:
    """Step 1 autoconfig succeeds → provider returned, no MX/LLM lookup."""
    provider, _mx_hosts = _detect_settings(
        email="user@example.com",
        api_key=None,
        llm_provider=None,
        autoconfig_lookup=_mock_autoconfig,
        mx_lookup=_mock_mx,
        provider_from_mx=_mock_provider_from_mx,
        detect_provider=_mock_detect,
        _detection_error=DetectionError,
    )
    assert provider is not None
    assert provider.imap_host == "imap.autoconfig.com"
    assert provider.smtp_host == "smtp.autoconfig.com"
    captured = capsys.readouterr()
    assert "autoconfig: imap=" in captured.err


def test_detect_settings_mx_hit(capsys: pytest.CaptureFixture[str]) -> None:
    """Autoconfig miss → MX lookup → provider_from_mx hit."""
    provider, mx_hosts = _detect_settings(
        email="user@example.com",
        api_key=None,
        llm_provider=None,
        autoconfig_lookup=_mock_autoconfig_none,
        mx_lookup=_mock_mx,
        provider_from_mx=_mock_provider_from_mx,
        detect_provider=_mock_detect,
        _detection_error=DetectionError,
    )
    assert provider is not None
    assert provider.imap_host == "imap.mx.com"
    assert mx_hosts == ["mx1.example.com", "mx2.example.com"]
    captured = capsys.readouterr()
    assert "MX:" in captured.err
    assert "MX provider:" in captured.err


def test_detect_settings_mx_empty_no_provider(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Autoconfig miss, no MX records → falls through to LLM."""
    provider, _mx_hosts = _detect_settings(
        email="user@example.com",
        api_key="sk-test",
        llm_provider=None,
        autoconfig_lookup=_mock_autoconfig_none,
        mx_lookup=_mock_mx_empty,
        provider_from_mx=_mock_provider_from_mx,
        detect_provider=_mock_detect,
        _detection_error=DetectionError,
    )
    assert provider is not None
    assert provider.imap_host == "imap.llm.com"
    captured = capsys.readouterr()
    assert "no known provider — asking the LLM" in captured.err


def test_detect_settings_llm_hit(capsys: pytest.CaptureFixture[str]) -> None:
    """Autoconfig + MX both miss → LLM provides the answer."""

    def _mx_lookup(email: str) -> list[str]:
        return ["mx.unknown.com"]

    def _provider_from_mx(mx_hosts: list[str]) -> MailProvider | None:
        return None  # unknown MX host

    provider, _mx_hosts = _detect_settings(
        email="user@example.com",
        api_key="sk-test",
        llm_provider=None,
        autoconfig_lookup=_mock_autoconfig_none,
        mx_lookup=_mx_lookup,
        provider_from_mx=_provider_from_mx,
        detect_provider=_mock_detect,
        _detection_error=DetectionError,
    )
    assert provider is not None
    assert provider.imap_host == "imap.llm.com"
    captured = capsys.readouterr()
    assert "no known provider — asking the LLM" in captured.err
    assert "LLM: imap=" in captured.err


def test_detect_settings_all_miss(capsys: pytest.CaptureFixture[str]) -> None:
    """Autoconfig miss, MX miss, LLM raises DetectionError → (None, mx_hosts)."""
    provider, _mx_hosts = _detect_settings(
        email="user@example.com",
        api_key="sk-test",
        llm_provider=None,
        autoconfig_lookup=_mock_autoconfig_none,
        mx_lookup=_mock_mx_empty,
        provider_from_mx=_mock_provider_from_mx,
        detect_provider=_mock_detect_error,
        _detection_error=DetectionError,
    )
    assert provider is None
    captured = capsys.readouterr()
    assert "Error: LLM unavailable" in captured.err


# ---------------------------------------------------------------------------
# _refine_password
# ---------------------------------------------------------------------------


def _build_config(provider: MailProvider, password: str | None) -> MailConfig:
    return MailConfig(
        username="user@example.com",
        imap_host=provider.imap_host,
        smtp_host=provider.smtp_host,
        password=password or "",
    )


def test_refine_password_returns_new_config(capsys: pytest.CaptureFixture[str]) -> None:
    """Successful interactive re-entry → new config with updated password."""
    provider = MailProvider(imap_host="imap.test.com", smtp_host="smtp.test.com")
    with mock.patch("getpass.getpass", return_value="newpass"):
        outcome = _refine_password(_build_config, provider)
    assert outcome.config is not None
    assert outcome.config.password == "newpass"
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


# ---------------------------------------------------------------------------
# _refine_with_llm
# ---------------------------------------------------------------------------


def test_refine_with_llm_success(capsys: pytest.CaptureFixture[str]) -> None:
    """LLM returns a refined provider → outcome includes new config + provider."""
    provider = MailProvider(imap_host="imap.bad.com", smtp_host="smtp.bad.com")
    config = MailConfig(
        username="user@example.com",
        imap_host="imap.bad.com",
        smtp_host="smtp.bad.com",
        password="pw",
    )
    result = _VerifyResult(imap_ok=False, smtp_ok=True, imap_error="refused")

    def _detect(email: str, **kwargs: Any) -> MailProvider:
        return MailProvider(imap_host="imap.good.com", smtp_host="smtp.good.com")

    outcome = _refine_with_llm(
        _build_config,
        provider,
        config,
        result,
        email="user@example.com",
        api_key="sk-test",
        llm_provider=None,
        mx_hosts=["mx1.example.com"],
        detect_provider=_detect,
        _detection_error=DetectionError,
    )
    assert outcome.config is not None
    assert outcome.config.imap_host == "imap.good.com"
    assert outcome.config.smtp_host == "smtp.good.com"
    assert outcome.config.password == "pw"  # preserved from original
    assert outcome.provider is not None
    assert outcome.provider.imap_host == "imap.good.com"


def test_refine_with_llm_error(capsys: pytest.CaptureFixture[str]) -> None:
    """LLM raises DetectionError → no config/provider."""
    provider = MailProvider(imap_host="imap.bad.com", smtp_host="smtp.bad.com")
    config = MailConfig(
        username="user@example.com",
        imap_host="imap.bad.com",
        smtp_host="smtp.bad.com",
        password="pw",
    )
    result = _VerifyResult(imap_ok=False, smtp_ok=True, imap_error="refused")

    def _detect_error(email: str, **kwargs: Any) -> MailProvider:
        raise DetectionError("no can do")

    outcome = _refine_with_llm(
        _build_config,
        provider,
        config,
        result,
        email="user@example.com",
        api_key="sk-test",
        llm_provider=None,
        mx_hosts=[],
        detect_provider=_detect_error,
        _detection_error=DetectionError,
    )
    assert outcome.config is None
    assert outcome.provider is None
    captured = capsys.readouterr()
    assert "LLM refinement error: no can do" in captured.err


def test_refine_with_llm_returns_none(capsys: pytest.CaptureFixture[str]) -> None:
    """LLM returns None provider → no config/provider."""
    provider = MailProvider(imap_host="imap.bad.com", smtp_host="smtp.bad.com")
    config = MailConfig(
        username="user@example.com",
        imap_host="imap.bad.com",
        smtp_host="smtp.bad.com",
        password="pw",
    )
    result = _VerifyResult(imap_ok=False, smtp_ok=True, imap_error="refused")

    def _detect_none(email: str, **kwargs: Any) -> MailProvider | None:
        return None

    outcome = _refine_with_llm(
        _build_config,
        provider,
        config,
        result,
        email="user@example.com",
        api_key="sk-test",
        llm_provider=None,
        mx_hosts=[],
        detect_provider=_detect_none,
        _detection_error=DetectionError,
    )
    assert outcome.config is None
    assert outcome.provider is None


# ---------------------------------------------------------------------------
# _refine_manual
# ---------------------------------------------------------------------------


def test_refine_manual_updates_hosts(
    capsys: pytest.CaptureFixture[str], cfg: MailConfig
) -> None:
    """User provides new hosts → config updated."""
    result = _VerifyResult(imap_ok=False, smtp_ok=False, imap_error="refused")
    with mock.patch("builtins.input", side_effect=["new-imap.com", "new-smtp.com"]):
        outcome = _refine_manual(cfg, result)
    assert outcome.config is not None
    assert outcome.config.imap_host == "new-imap.com"
    assert outcome.config.smtp_host == "new-smtp.com"


def test_refine_manual_no_change(
    capsys: pytest.CaptureFixture[str], cfg: MailConfig
) -> None:
    """User presses Enter on both prompts → no config."""
    result = _VerifyResult(imap_ok=False, smtp_ok=False)
    with mock.patch("builtins.input", side_effect=["", ""]):
        outcome = _refine_manual(cfg, result)
    assert outcome.config is None


def test_refine_manual_eof(cfg: MailConfig) -> None:
    """EOFError during input → no config."""
    result = _VerifyResult(imap_ok=False, smtp_ok=False)
    with mock.patch("builtins.input", side_effect=EOFError):
        outcome = _refine_manual(cfg, result)
    assert outcome.config is None


# ---------------------------------------------------------------------------
# _report_failure
# ---------------------------------------------------------------------------


def test_report_failure(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """Prints the expected failure message referencing the output path."""
    output = tmp_path / "cfg.yaml"
    _report_failure(output)
    captured = capsys.readouterr()
    assert "Verification FAILED" in captured.err
    assert str(output) in captured.err


# ---------------------------------------------------------------------------
# _verify_and_refine (integration-style with mocked sub-functions)
# ---------------------------------------------------------------------------


def _provider_to_config(
    provider: MailProvider, email: str, password: str = ""
) -> MailConfig:
    return MailConfig(
        username=email,
        imap_host=provider.imap_host,
        smtp_host=provider.smtp_host,
        password=password,
    )


def test_verify_and_refine_success_first_try(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verification succeeds immediately → returns 0, config written."""
    output = tmp_path / "cfg.yaml"
    provider = MailProvider(imap_host="imap.ok.com", smtp_host="smtp.ok.com")

    with mock.patch(
        "robotsix_auto_mail.cli._verify_config",
        return_value=_VerifyResult(imap_ok=True, smtp_ok=True),
    ):
        rc = _verify_and_refine(
            provider,
            email="user@example.com",
            api_key=None,
            llm_provider=None,
            mx_hosts=[],
            output_path=output,
            password="pw",
            password_from_args="pw",
            no_verify=False,
            account_id="default",
            label=None,
            provider_to_config=_provider_to_config,
            detect_provider=_mock_detect,
            _detection_error=DetectionError,
        )

    assert rc == 0
    assert output.exists()
    content = output.read_text()
    assert "imap.ok.com" in content


def test_verify_and_refine_auth_failure_with_retry_budget(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Auth failure with interactive password → re-prompt happens, then success."""
    output = tmp_path / "cfg.yaml"
    provider = MailProvider(imap_host="imap.ok.com", smtp_host="smtp.ok.com")

    # First verify: auth failure (password wrong), then second: success
    verify_results = [
        _VerifyResult(
            imap_ok=False,
            smtp_ok=False,
            imap_auth=True,
            smtp_auth=True,
            imap_error="auth",
            smtp_error="auth",
        ),
        _VerifyResult(imap_ok=True, smtp_ok=True),
    ]

    with (
        mock.patch(
            "robotsix_auto_mail.cli._verify_config",
            side_effect=verify_results,
        ),
        mock.patch("getpass.getpass", return_value="new-correct-pw"),
    ):
        rc = _verify_and_refine(
            provider,
            email="user@example.com",
            api_key=None,
            llm_provider=None,
            mx_hosts=[],
            output_path=output,
            password="wrong-pw",
            password_from_args=None,  # interactive → retry budget available
            no_verify=False,
            account_id="default",
            label=None,
            provider_to_config=_provider_to_config,
            detect_provider=_mock_detect,
            _detection_error=DetectionError,
        )

    assert rc == 0
    captured = capsys.readouterr()
    assert "password was rejected" in captured.err


def test_verify_and_refine_auth_failure_no_retry_with_args_password(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Auth failure with --password supplied → no retry budget, returns 1."""
    output = tmp_path / "cfg.yaml"
    provider = MailProvider(imap_host="imap.ok.com", smtp_host="smtp.ok.com")

    with mock.patch(
        "robotsix_auto_mail.cli._verify_config",
        return_value=_VerifyResult(
            imap_ok=False,
            smtp_ok=False,
            imap_auth=True,
            smtp_auth=True,
            imap_error="auth",
            smtp_error="auth",
        ),
    ):
        rc = _verify_and_refine(
            provider,
            email="user@example.com",
            api_key=None,
            llm_provider=None,
            mx_hosts=[],
            output_path=output,
            password="cli-pass",
            password_from_args="cli-pass",  # from --password → budget = 0
            no_verify=False,
            account_id="default",
            label=None,
            provider_to_config=_provider_to_config,
            detect_provider=_mock_detect,
            _detection_error=DetectionError,
        )

    assert rc == 1
    captured = capsys.readouterr()
    assert "Verification FAILED" in captured.err


def test_verify_and_refine_host_failure_llm_refine(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Host failure → LLM refines provider → then verify succeeds."""
    output = tmp_path / "cfg.yaml"
    provider = MailProvider(imap_host="imap.bad.com", smtp_host="smtp.bad.com")

    verify_results = [
        _VerifyResult(
            imap_ok=False,
            smtp_ok=True,
            imap_error="refused",
        ),
        _VerifyResult(imap_ok=True, smtp_ok=True),
    ]

    refined_provider = MailProvider(
        imap_host="imap.good.com", smtp_host="smtp.good.com"
    )

    def _refine_detect(email: str, **kwargs: Any) -> MailProvider:
        return refined_provider

    with (
        mock.patch(
            "robotsix_auto_mail.cli._verify_config",
            side_effect=verify_results,
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = _verify_and_refine(
            provider,
            email="user@example.com",
            api_key="sk-test",
            llm_provider=None,
            mx_hosts=["mx.example.com"],
            output_path=output,
            password="pw",
            password_from_args="pw",
            no_verify=False,
            account_id="default",
            label=None,
            provider_to_config=_provider_to_config,
            detect_provider=_refine_detect,
            _detection_error=DetectionError,
        )

    assert rc == 0
    content = output.read_text()
    assert "imap.good.com" in content


def test_verify_and_refine_host_failure_llm_then_manual(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Host failure → LLM fails → manual prompt succeeds on next verify."""
    output = tmp_path / "cfg.yaml"
    provider = MailProvider(imap_host="imap.bad.com", smtp_host="smtp.bad.com")

    verify_results = [
        _VerifyResult(
            imap_ok=False,
            smtp_ok=False,
            imap_error="refused",
            smtp_error="refused",
        ),
        _VerifyResult(imap_ok=True, smtp_ok=True),
    ]

    def _refine_detect_error(email: str, **kwargs: Any) -> MailProvider:
        raise DetectionError("LLM failed")

    with (
        mock.patch(
            "robotsix_auto_mail.cli._verify_config",
            side_effect=verify_results,
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
        mock.patch("builtins.input", side_effect=["", "manual-smtp.com"]),
    ):
        rc = _verify_and_refine(
            provider,
            email="user@example.com",
            api_key="sk-test",
            llm_provider=None,
            mx_hosts=[],
            output_path=output,
            password="pw",
            password_from_args="pw",
            no_verify=False,
            account_id="default",
            label=None,
            provider_to_config=_provider_to_config,
            detect_provider=_refine_detect_error,
            _detection_error=DetectionError,
        )

    assert rc == 0
    content = output.read_text()
    assert "manual-smtp.com" in content


def test_verify_and_refine_microsoft_no_password_retry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Microsoft accounts: auth failure shows consent message, no password retry."""
    output = tmp_path / "cfg.yaml"
    provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with (
        mock.patch(
            "robotsix_auto_mail.cli._verify_config",
            return_value=_VerifyResult(
                imap_ok=False,
                smtp_ok=False,
                imap_auth=True,
                smtp_auth=True,
                imap_error="auth",
                smtp_error="auth",
            ),
        ),
        mock.patch("robotsix_auto_mail.oauth2.device_code_login"),
    ):
        rc = _verify_and_refine(
            provider,
            email="user@contoso.com",
            api_key=None,
            llm_provider=None,
            mx_hosts=[],
            output_path=output,
            password=None,
            password_from_args=None,
            no_verify=False,
            account_id="default",
            label=None,
            provider_to_config=_provider_to_config,
            detect_provider=_mock_detect,
            _detection_error=DetectionError,
            microsoft=True,
        )

    assert rc == 1
    captured = capsys.readouterr()
    assert "XOAUTH2 authentication failed" in captured.err


def test_verify_and_refine_microsoft_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Microsoft account: device-code login succeeds, verification passes."""
    output = tmp_path / "cfg.yaml"
    provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with (
        mock.patch("robotsix_auto_mail.oauth2.device_code_login"),
        mock.patch(
            "robotsix_auto_mail.cli._verify_config",
            return_value=_VerifyResult(imap_ok=True, smtp_ok=True),
        ),
    ):
        rc = _verify_and_refine(
            provider,
            email="user@contoso.com",
            api_key=None,
            llm_provider=None,
            mx_hosts=[],
            output_path=output,
            password=None,
            password_from_args=None,
            no_verify=False,
            account_id="default",
            label=None,
            provider_to_config=_provider_to_config,
            detect_provider=_mock_detect,
            _detection_error=DetectionError,
            microsoft=True,
        )

    assert rc == 0
    captured = capsys.readouterr()
    assert "device-code login" in captured.err


def test_verify_and_refine_no_password_no_verify(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No password + no_verify → returns 0, config written, instruction printed."""
    output = tmp_path / "cfg.yaml"
    provider = MailProvider(imap_host="imap.test.com", smtp_host="smtp.test.com")

    rc = _verify_and_refine(
        provider,
        email="user@example.com",
        api_key=None,
        llm_provider=None,
        mx_hosts=[],
        output_path=output,
        password=None,
        password_from_args=None,
        no_verify=True,
        account_id="default",
        label=None,
        provider_to_config=_provider_to_config,
        detect_provider=_mock_detect,
        _detection_error=DetectionError,
    )

    assert rc == 0
    assert output.exists()
    captured = capsys.readouterr()
    assert "Config written" in captured.err


def test_verify_and_refine_budget_exhausted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """LLM refines exhaust budget → manual fails → returns 1."""
    output = tmp_path / "cfg.yaml"
    provider = MailProvider(imap_host="imap.bad.com", smtp_host="smtp.bad.com")

    verify_results = [
        _VerifyResult(imap_ok=False, smtp_ok=False, imap_error="refused"),
        _VerifyResult(imap_ok=False, smtp_ok=False, imap_error="still refused"),
        _VerifyResult(imap_ok=False, smtp_ok=False, imap_error="nope"),
        _VerifyResult(imap_ok=False, smtp_ok=False, imap_error="final"),
    ]

    def _refine_detect_error(email: str, **kwargs: Any) -> MailProvider:
        raise DetectionError("LLM failed")

    with (
        mock.patch(
            "robotsix_auto_mail.cli._verify_config",
            side_effect=verify_results,
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
        mock.patch("builtins.input", side_effect=["", ""]),
    ):
        rc = _verify_and_refine(
            provider,
            email="user@example.com",
            api_key="sk-test",
            llm_provider=None,
            mx_hosts=[],
            output_path=output,
            password="pw",
            password_from_args="pw",
            no_verify=False,
            account_id="default",
            label=None,
            provider_to_config=_provider_to_config,
            detect_provider=_refine_detect_error,
            _detection_error=DetectionError,
        )

    assert rc == 1
    captured = capsys.readouterr()
    assert "Verification FAILED" in captured.err


def test_verify_and_refine_multi_account_append(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Existing multi-account file: new account is appended, others preserved."""
    output = tmp_path / "accounts.yaml"
    output.write_text(
        "accounts:\n"
        "  - id: existing\n"
        "    auth:\n"
        "      username: old@example.com\n"
        "    imap:\n"
        "      host: imap.old.com\n"
        "    smtp:\n"
        "      host: smtp.old.com\n"
        "default_account_id: existing\n"
    )
    provider = MailProvider(imap_host="imap.new.com", smtp_host="smtp.new.com")

    with mock.patch(
        "robotsix_auto_mail.cli._verify_config",
        return_value=_VerifyResult(imap_ok=True, smtp_ok=True),
    ):
        rc = _verify_and_refine(
            provider,
            email="new@example.com",
            api_key=None,
            llm_provider=None,
            mx_hosts=[],
            output_path=output,
            password="pw",
            password_from_args="pw",
            no_verify=False,
            account_id="new-account",
            label="New Account",
            provider_to_config=_provider_to_config,
            detect_provider=_mock_detect,
            _detection_error=DetectionError,
        )

    assert rc == 0
    content = output.read_text()
    assert "existing" in content
    assert "new-account" in content
    assert "old@example.com" in content
    assert "new@example.com" in content
