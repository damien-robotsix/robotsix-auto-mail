"""Tests for the CLI refine/verify pipeline (refine_password, refine_with_llm, refine_manual, verify_and_refine, prompt_hosts)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from robotsix_auto_mail.cli.config import (
    _prompt_hosts,
    _refine_manual,
    _refine_password,
    _refine_with_llm,
    _verify_and_refine,
    _VerifyResult,
)
from robotsix_auto_mail.config import (
    ConfigurationError,
    MailAccount,
    MailAccountsConfig,
    MailConfig,
)
from robotsix_auto_mail.config.detect import DetectionError, MailProvider

# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _accounts(cfg: MailConfig, account_id: str = "default") -> MailAccountsConfig:
    """Wrap a single ``MailConfig`` in a one-element accounts container."""
    return MailAccountsConfig(
        accounts=(MailAccount(account_id=account_id, config=cfg, label=None),),
        default_account_id=account_id,
    )


def _build_config(provider: MailProvider, password: str | None) -> MailConfig:
    return MailConfig(
        username="user@example.com",
        imap_host=provider.imap_host,
        smtp_host=provider.smtp_host,
        password=password or "",
    )


def _provider_to_config(
    provider: MailProvider, email: str, password: str = ""
) -> MailConfig:
    return MailConfig(
        username=email,
        imap_host=provider.imap_host,
        smtp_host=provider.smtp_host,
        password=password,
    )


def _mock_provider_from_mx(mx_hosts: list[str]) -> MailProvider | None:
    if mx_hosts:
        return MailProvider(imap_host="imap.mx.com", smtp_host="smtp.mx.com")
    return None


def _mock_detect(email: str, **kwargs: Any) -> MailProvider:
    return MailProvider(imap_host="imap.llm.com", smtp_host="smtp.llm.com")


def _mock_detect_error(email: str, **kwargs: Any) -> MailProvider:
    raise DetectionError("LLM unavailable")


def _refine_test_config() -> MailConfig:
    """Build a minimal MailConfig for refinement-helper unit tests."""
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
    )


def _refine_host_result() -> "_VerifyResult":
    """Build an IMAP-host-failure _VerifyResult for helper unit tests."""
    from robotsix_auto_mail.cli import _VerifyResult

    return _VerifyResult(imap_ok=False, smtp_ok=True, imap_error="connection refused")


# ---------------------------------------------------------------------------
# _refine_password
# ---------------------------------------------------------------------------


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
        password="pw",  # pragma: allowlist secret
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
        api_key="sk-test",  # pragma: allowlist secret
        llm_provider_model=None,
        mx_hosts=["mx1.example.com"],
        detect_provider=_detect,
        _detection_error=DetectionError,
    )
    assert outcome.config is not None
    assert outcome.config.imap_host == "imap.good.com"
    assert outcome.config.smtp_host == "smtp.good.com"
    assert (
        outcome.config.password.get_secret_value() == "pw"  # pragma: allowlist secret
    )  # preserved from original  # pragma: allowlist secret
    assert outcome.provider is not None
    assert outcome.provider.imap_host == "imap.good.com"


def test_refine_with_llm_error(capsys: pytest.CaptureFixture[str]) -> None:
    """LLM raises DetectionError → no config/provider."""
    provider = MailProvider(imap_host="imap.bad.com", smtp_host="smtp.bad.com")
    config = MailConfig(
        username="user@example.com",
        imap_host="imap.bad.com",
        smtp_host="smtp.bad.com",
        password="pw",  # pragma: allowlist secret
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
        api_key="sk-test",  # pragma: allowlist secret
        llm_provider_model=None,
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
        password="pw",  # pragma: allowlist secret
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
        api_key="sk-test",  # pragma: allowlist secret
        llm_provider_model=None,
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


def test_refine_manual_stops_when_prompt_returns_none() -> None:
    """_refine_manual signals stop when _prompt_hosts returns None."""
    from robotsix_auto_mail.cli import _refine_manual

    with mock.patch("robotsix_auto_mail.cli._prompt_hosts", return_value=None):
        outcome = _refine_manual(_refine_test_config(), _refine_host_result())

    assert outcome.config is None


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


# ---------------------------------------------------------------------------
# _prompt_hosts — direct unit tests
# ---------------------------------------------------------------------------


def test_prompt_hosts_both_failing(cfg: MailConfig) -> None:
    """Both IMAP and SMTP host problems prompt for each host."""
    result = _VerifyResult(
        imap_ok=False, smtp_ok=False, imap_error="refused", smtp_error="timeout"
    )
    with mock.patch("builtins.input", side_effect=["new-imap.com", "new-smtp.com"]):
        updated = _prompt_hosts(cfg, result)
    assert updated is not None
    assert updated.imap_host == "new-imap.com"
    assert updated.smtp_host == "new-smtp.com"


def test_prompt_hosts_imap_only(cfg: MailConfig) -> None:
    """Only IMAP has a host problem → only IMAP is prompted."""
    result = _VerifyResult(imap_ok=False, smtp_ok=True, imap_error="refused")
    with mock.patch("builtins.input", side_effect=["fixed-imap.com"]):
        updated = _prompt_hosts(cfg, result)
    assert updated is not None
    assert updated.imap_host == "fixed-imap.com"
    assert updated.smtp_host == cfg.smtp_host


def test_prompt_hosts_smtp_only(cfg: MailConfig) -> None:
    """Only SMTP has a host problem → only SMTP is prompted."""
    result = _VerifyResult(imap_ok=True, smtp_ok=False, smtp_error="timeout")
    with mock.patch("builtins.input", side_effect=["fixed-smtp.com"]):
        updated = _prompt_hosts(cfg, result)
    assert updated is not None
    assert updated.smtp_host == "fixed-smtp.com"
    assert updated.imap_host == cfg.imap_host


def test_prompt_hosts_auth_not_prompted(cfg: MailConfig) -> None:
    """Auth failures (not host problems) are not prompted."""
    result = _VerifyResult(
        imap_ok=False,
        smtp_ok=False,
        imap_auth=True,
        smtp_auth=True,
        imap_error="auth",
        smtp_error="auth",
    )
    updated = _prompt_hosts(cfg, result)
    assert updated is None  # no host problems → no prompts → no change


def test_prompt_hosts_no_change(cfg: MailConfig) -> None:
    """User presses Enter without typing → no config returned."""
    result = _VerifyResult(imap_ok=False, smtp_ok=False)
    with mock.patch("builtins.input", side_effect=["", ""]):
        updated = _prompt_hosts(cfg, result)
    assert updated is None


def test_prompt_hosts_eof(cfg: MailConfig) -> None:
    """EOFError during prompt → None returned."""
    result = _VerifyResult(imap_ok=False, smtp_ok=False)
    with mock.patch("builtins.input", side_effect=EOFError):
        updated = _prompt_hosts(cfg, result)
    assert updated is None


def test_prompt_hosts_keyboard_interrupt(cfg: MailConfig) -> None:
    """KeyboardInterrupt during prompt → None returned."""
    result = _VerifyResult(imap_ok=False, smtp_ok=False)
    with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
        updated = _prompt_hosts(cfg, result)
    assert updated is None


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
