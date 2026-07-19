"""Integration-pipeline tests for _verify_and_refine."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from robotsix_auto_mail.cli.config import _verify_and_refine, _VerifyResult
from robotsix_auto_mail.config import ConfigurationError
from robotsix_auto_mail.config.detect import DetectionError, MailProvider
from tests.cli.conftest import _mock_detect, _provider_to_config

# ---------------------------------------------------------------------------
# _verify_and_refine (integration-style with mocked sub-functions)
# ---------------------------------------------------------------------------


def test_verify_and_refine_success_first_try(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verification succeeds immediately → returns 0, config returned."""
    provider = MailProvider(imap_host="imap.ok.com", smtp_host="smtp.ok.com")

    with mock.patch(
        "robotsix_auto_mail.cli._verify_config",
        return_value=_VerifyResult(imap_ok=True, smtp_ok=True),
    ):
        rc, config = _verify_and_refine(
            provider,
            email="user@example.com",
            api_key=None,
            llm_provider_model=None,
            mx_hosts=[],
            password="pw",  # pragma: allowlist secret
            password_from_args="pw",  # pragma: allowlist secret
            no_verify=False,
            account_id="default",
            label=None,
            provider_to_config=_provider_to_config,
            detect_provider=_mock_detect,
            _detection_error=DetectionError,
        )

    assert rc == 0
    assert config is not None
    assert config.imap_host == "imap.ok.com"


def test_verify_and_refine_auth_failure_with_retry_budget(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Auth failure with interactive password → re-prompt happens, then success."""
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
        rc, config = _verify_and_refine(
            provider,
            email="user@example.com",
            api_key=None,
            llm_provider_model=None,
            mx_hosts=[],
            password="wrong-pw",  # pragma: allowlist secret
            password_from_args=None,  # interactive → retry budget available
            no_verify=False,
            account_id="default",
            label=None,
            provider_to_config=_provider_to_config,
            detect_provider=_mock_detect,
            _detection_error=DetectionError,
        )

    assert rc == 0
    assert config is not None
    captured = capsys.readouterr()
    assert "password was rejected" in captured.err


def test_verify_and_refine_auth_failure_no_retry_with_args_password(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Auth failure with --password supplied → no retry budget, returns 1."""
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
        rc, config = _verify_and_refine(
            provider,
            email="user@example.com",
            api_key=None,
            llm_provider_model=None,
            mx_hosts=[],
            password="cli-pass",  # pragma: allowlist secret
            password_from_args="cli-pass",  # from --password → budget = 0  # pragma: allowlist secret
            no_verify=False,
            account_id="default",
            label=None,
            provider_to_config=_provider_to_config,
            detect_provider=_mock_detect,
            _detection_error=DetectionError,
        )

    assert rc == 1
    assert config is not None
    captured = capsys.readouterr()
    assert "Verification FAILED" in captured.err


def test_verify_and_refine_host_failure_llm_refine(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Host failure → LLM refines provider → then verify succeeds."""
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
        mock.patch.dict(
            os.environ,
            {"LLM_API_KEY": "sk-test"},  # pragma: allowlist secret
        ),  # pragma: allowlist secret
    ):
        rc, config = _verify_and_refine(
            provider,
            email="user@example.com",
            api_key="sk-test",  # pragma: allowlist secret
            llm_provider_model=None,
            mx_hosts=["mx.example.com"],
            password="pw",  # pragma: allowlist secret
            password_from_args="pw",  # pragma: allowlist secret
            no_verify=False,
            account_id="default",
            label=None,
            provider_to_config=_provider_to_config,
            detect_provider=_refine_detect,
            _detection_error=DetectionError,
        )

    assert rc == 0
    assert config is not None
    assert config.imap_host == "imap.good.com"


def test_verify_and_refine_host_failure_llm_then_manual(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Host failure → LLM fails → manual prompt succeeds on next verify."""
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
        mock.patch.dict(
            os.environ,
            {"LLM_API_KEY": "sk-test"},  # pragma: allowlist secret
        ),  # pragma: allowlist secret
        mock.patch("builtins.input", side_effect=["", "manual-smtp.com"]),
    ):
        rc, config = _verify_and_refine(
            provider,
            email="user@example.com",
            api_key="sk-test",  # pragma: allowlist secret
            llm_provider_model=None,
            mx_hosts=[],
            password="pw",  # pragma: allowlist secret
            password_from_args="pw",  # pragma: allowlist secret
            no_verify=False,
            account_id="default",
            label=None,
            provider_to_config=_provider_to_config,
            detect_provider=_refine_detect_error,
            _detection_error=DetectionError,
        )

    assert rc == 0
    assert config is not None
    assert config.smtp_host == "manual-smtp.com"


def test_verify_and_refine_microsoft_no_password_retry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Microsoft accounts: auth failure shows consent message, no password retry."""
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
        rc, config = _verify_and_refine(
            provider,
            email="user@contoso.com",
            api_key=None,
            llm_provider_model=None,
            mx_hosts=[],
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
    assert config is not None
    captured = capsys.readouterr()
    assert "XOAUTH2 authentication failed" in captured.err


def test_verify_and_refine_microsoft_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Microsoft account: device-code login succeeds, verification passes."""
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
        rc, config = _verify_and_refine(
            provider,
            email="user@contoso.com",
            api_key=None,
            llm_provider_model=None,
            mx_hosts=[],
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
    assert config is not None
    captured = capsys.readouterr()
    assert "device-code login" in captured.err


def test_verify_and_refine_no_password_no_verify(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No password + no_verify → returns 0, config written, instruction printed."""
    provider = MailProvider(imap_host="imap.test.com", smtp_host="smtp.test.com")

    rc, config = _verify_and_refine(
        provider,
        email="user@example.com",
        api_key=None,
        llm_provider_model=None,
        mx_hosts=[],
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
    assert config is not None
    captured = capsys.readouterr()
    assert "No password provided" in captured.err


def test_verify_and_refine_budget_exhausted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """LLM refines exhaust budget → manual fails → returns 1."""
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
        mock.patch.dict(
            os.environ,
            {"LLM_API_KEY": "sk-test"},  # pragma: allowlist secret
        ),  # pragma: allowlist secret
        mock.patch("builtins.input", side_effect=["", ""]),
    ):
        rc, config = _verify_and_refine(
            provider,
            email="user@example.com",
            api_key="sk-test",  # pragma: allowlist secret
            llm_provider_model=None,
            mx_hosts=[],
            password="pw",  # pragma: allowlist secret
            password_from_args="pw",  # pragma: allowlist secret
            no_verify=False,
            account_id="default",
            label=None,
            provider_to_config=_provider_to_config,
            detect_provider=_refine_detect_error,
            _detection_error=DetectionError,
        )

    assert rc == 1
    assert config is not None
    captured = capsys.readouterr()
    assert "Verification FAILED" in captured.err


def test_verify_and_refine_multi_account_append(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Existing multi-account file: new account is appended, others preserved."""
    output = tmp_path / "accounts.yaml"
    output.write_text(
        '{"accounts": ['
        '{"account_id": "existing", "label": "Existing", "config": {'
        '"imap_host": "imap.old.com", "smtp_host": "smtp.old.com",'
        '"username": "old@example.com", "password": ""'
        "}}"
        '], "default_account_id": "existing"}'
    )
    provider = MailProvider(imap_host="imap.new.com", smtp_host="smtp.new.com")

    with mock.patch(
        "robotsix_auto_mail.cli._verify_config",
        return_value=_VerifyResult(imap_ok=True, smtp_ok=True),
    ):
        rc, config = _verify_and_refine(
            provider,
            email="new@example.com",
            api_key=None,
            llm_provider_model=None,
            mx_hosts=[],
            password="pw",  # pragma: allowlist secret
            password_from_args="pw",  # pragma: allowlist secret
            no_verify=False,
            account_id="new-account",
            label="New Account",
            provider_to_config=_provider_to_config,
            detect_provider=_mock_detect,
            _detection_error=DetectionError,
        )

    assert rc == 0
    assert config is not None


# _verify_and_refine — remaining edge cases
# ---------------------------------------------------------------------------


def test_verify_and_refine_microsoft_no_verify(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Microsoft account with --no-verify returns 0 immediately after writing config."""
    provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    rc, config = _verify_and_refine(
        provider,
        email="user@contoso.com",
        api_key=None,
        llm_provider_model=None,
        mx_hosts=[],
        password=None,
        password_from_args=None,
        no_verify=True,
        account_id="default",
        label=None,
        provider_to_config=_provider_to_config,
        detect_provider=_mock_detect,
        _detection_error=DetectionError,
        microsoft=True,
    )

    assert rc == 0
    assert config is not None


def test_verify_and_refine_microsoft_device_code_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Microsoft device-code login raises ConfigurationError → return 1."""
    provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with mock.patch(
        "robotsix_auto_mail.oauth2.device_code_login",
        side_effect=ConfigurationError("missing tenant"),
    ):
        rc, config = _verify_and_refine(
            provider,
            email="user@contoso.com",
            api_key=None,
            llm_provider_model=None,
            mx_hosts=[],
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
    assert config is None
    captured = capsys.readouterr()
    assert "missing tenant" in captured.err


def test_verify_and_refine_microsoft_device_code_exception(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Microsoft device-code login raises generic Exception → return 1."""
    provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with mock.patch(
        "robotsix_auto_mail.oauth2.device_code_login",
        side_effect=RuntimeError("network down"),
    ):
        rc, config = _verify_and_refine(
            provider,
            email="user@contoso.com",
            api_key=None,
            llm_provider_model=None,
            mx_hosts=[],
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
    assert config is None
    captured = capsys.readouterr()
    assert "device-code login failed" in captured.err


def test_verify_and_refine_no_password_early_return(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Non-Microsoft, no password, no_verify=False → returns 0 with instructions."""
    provider = MailProvider(imap_host="imap.test.com", smtp_host="smtp.test.com")

    rc, config = _verify_and_refine(
        provider,
        email="user@example.com",
        api_key=None,
        llm_provider_model=None,
        mx_hosts=[],
        password=None,
        password_from_args=None,
        no_verify=False,
        account_id="default",
        label=None,
        provider_to_config=_provider_to_config,
        detect_provider=_mock_detect,
        _detection_error=DetectionError,
    )

    assert rc == 0
    assert config is not None
    captured = capsys.readouterr()
    assert "No password provided" in captured.err


def test_verify_and_refine_auth_retry_returns_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Auth failure → password re-prompt returns None → break → return 1."""
    provider = MailProvider(imap_host="imap.ok.com", smtp_host="smtp.ok.com")

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
        mock.patch("getpass.getpass", return_value=""),  # empty → None
    ):
        rc, config = _verify_and_refine(
            provider,
            email="user@example.com",
            api_key=None,
            llm_provider_model=None,
            mx_hosts=[],
            password="wrong-pw",  # pragma: allowlist secret
            password_from_args=None,  # interactive → pw_budget = 2
            no_verify=False,
            account_id="default",
            label=None,
            provider_to_config=_provider_to_config,
            detect_provider=_mock_detect,
            _detection_error=DetectionError,
        )

    assert rc == 1
    assert config is not None
    captured = capsys.readouterr()
    assert "Verification FAILED" in captured.err


def test_verify_and_refine_password_with_no_verify(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Non-Microsoft, password present, --no-verify → returns 0, config written."""
    provider = MailProvider(imap_host="imap.test.com", smtp_host="smtp.test.com")

    rc, config = _verify_and_refine(
        provider,
        email="user@example.com",
        api_key=None,
        llm_provider_model=None,
        mx_hosts=[],
        password="pw",  # pragma: allowlist secret
        password_from_args="pw",  # pragma: allowlist secret
        no_verify=True,
        account_id="default",
        label=None,
        provider_to_config=_provider_to_config,
        detect_provider=_mock_detect,
        _detection_error=DetectionError,
    )

    assert rc == 0
    assert config is not None
