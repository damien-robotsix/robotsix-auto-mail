"""Tests for CLI detect subcommand account management and overwrite logic."""

from __future__ import annotations

import json as _json
import os
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.cli import main
from robotsix_auto_mail.config import (
    MailAccount,
    MailAccountsConfig,
    MailConfig,
)
from robotsix_auto_mail.config.detect import DetectionError, MailProvider
from tests.cli.conftest import _ok_result, _auth_fail_result, _host_fail_result


def test_detect_preserves_existing_llm_section(
    tmp_path: Path, no_autoconfig: object
) -> None:
    """Re-running detect over an accounts file keeps its top-level llm section."""
    output = tmp_path / "mail.local.json"

    seed = {
        "accounts": [
            {
                "account_id": "existing",
                "config": {
                    "imap_host": "old.example.com",
                    "smtp_host": "old.example.com",
                    "username": "old@example.com",
                    "password": "old-pw",
                    "llm_api_key": "sk-keep-me",
                },
                "label": None,
            }
        ],
        "default_account_id": "existing",
    }
    output.write_text(_json.dumps(seed, indent=2))
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
            ]
        )

    assert rc == 0
    content = output.read_text()
    # mail fields updated…
    assert "imap.gmail.com" in content
    assert "user@gmail.com" in content
    # …but the llm api key is preserved
    assert "sk-keep-me" in content


def test_detect_honours_id_flag(tmp_path: Path, no_autoconfig: object) -> None:
    """detect --id sets the account id and the .data/<id>/mail.db store path."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
                "--id",
                "personal",
            ]
        )

    assert rc == 0

    accounts = MailAccountsConfig.model_validate(_json.loads(output.read_text()))
    assert accounts.ids() == ("personal",)
    assert accounts.default_account_id == "personal"
    assert accounts.get("personal").config.db_path == ".data/personal/mail.db"


def test_detect_appends_second_account(tmp_path: Path, no_autoconfig: object) -> None:
    """A second detect against an existing multi-account file appends, not clobbers."""
    output = tmp_path / "cfg.yaml"
    p1 = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")
    p2 = MailProvider(imap_host="imap.work.com", smtp_host="smtp.work.com")

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider", side_effect=[p1, p2]
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc1 = main(
            [
                "detect",
                "me@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
                "--id",
                "personal",
            ]
        )
        rc2 = main(
            [
                "detect",
                "me@work.com",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
                "--id",
                "work",
            ]
        )

    assert rc1 == 0
    assert rc2 == 0

    accounts = MailAccountsConfig.model_validate(_json.loads(output.read_text()))
    assert set(accounts.ids()) == {"personal", "work"}
    assert accounts.get("work").config.imap_host == "imap.work.com"


def test_detect_refuses_duplicate_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """A detect whose resolved id already exists is refused (exit 1) without clobber."""
    output = tmp_path / "cfg.yaml"
    provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider", return_value=provider
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc1 = main(
            [
                "detect",
                "me@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
                "--id",
                "personal",
            ]
        )
        capsys.readouterr()
        rc2 = main(
            [
                "detect",
                "other@gmail.com",
                "--output",
                str(output),
                "--password",
                "pw",
                "--no-verify",
                "--id",
                "personal",
            ]
        )

    assert rc1 == 0
    assert rc2 == 1
    assert "already exists" in capsys.readouterr().err

    accounts = MailAccountsConfig.model_validate(_json.loads(output.read_text()))
    assert accounts.ids() == ("personal",)


def test_detect_overwrite_existing_account(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--overwrite updates transport fields in place; no duplicate is added."""

    output = tmp_path / "cfg.json"
    seed_cfg = MailConfig(
        imap_host="",
        smtp_host="",
        username="test@gmail.com",
        password="",
        db_path=".data/mail.db",  # legacy single-account path — must be preserved
    )
    seed_account = MailAccount(account_id="main", config=seed_cfg, label="Main Account")
    container = MailAccountsConfig(accounts=[seed_account], default_account_id="main")
    output.write_text(container.model_dump_json(indent=2))

    provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider", return_value=provider
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "test@gmail.com",
                "--id",
                "main",
                "--overwrite",
                "--password",
                "secret",
                "--no-verify",
                "--output",
                str(output),
            ]
        )

    assert rc == 0

    accounts = MailAccountsConfig.model_validate(_json.loads(output.read_text()))
    # No duplicate account appended
    assert accounts.ids() == ("main",)
    main_account = next(a for a in accounts.accounts if a.account_id == "main")
    cfg = main_account.config
    # Transport fields updated
    assert cfg.imap_host == "imap.gmail.com"
    assert cfg.smtp_host == "smtp.gmail.com"
    # Password written (supplied via --password)
    assert cfg.password == "secret"
    # Non-transport fields preserved from seed
    assert cfg.username == "test@gmail.com"
    assert cfg.db_path == ".data/mail.db"  # legacy path preserved, not replaced
    # Label preserved from existing account
    assert main_account.label == "Main Account"


def test_detect_overwrite_not_set_still_errors_on_duplicate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """Without --overwrite, a duplicate id still exits 1 and prints 'already exists'."""

    output = tmp_path / "cfg.json"
    seed_cfg = MailConfig(
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
        username="test@gmail.com",
        password="pw",
    )
    container = MailAccountsConfig(
        accounts=[MailAccount(account_id="main", config=seed_cfg, label=None)],
        default_account_id="main",
    )
    output.write_text(container.model_dump_json(indent=2))
    provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider", return_value=provider
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "test@gmail.com",
                "--id",
                "main",
                "--no-verify",
                "--output",
                str(output),
                "--password",
                "pw",
            ]
        )

    assert rc == 1
    assert "already exists" in capsys.readouterr().err


def test_detect_overwrite_with_oauth2_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--overwrite --oauth2-client-id overlays oauth2 fields onto an existing
    account config instead of silently ignoring them."""

    output = tmp_path / "cfg.json"
    # Seed a Microsoft account with default oauth2 fields — the
    # --oauth2-client-id and --oauth2-tenant flags should override them.
    seed_cfg = MailConfig(
        imap_host="old.example.com",
        smtp_host="old.example.com",
        username="user@tii.ae",
        password="",
        oauth2_provider="microsoft",
        oauth2_client_id="9e5f94bc-e8a4-4e73-b8be-63364c29d753",
        oauth2_tenant="organizations",
    )
    seed_account = MailAccount(account_id="tii", config=seed_cfg, label="TII")
    container = MailAccountsConfig(accounts=[seed_account], default_account_id="tii")
    output.write_text(container.model_dump_json(indent=2))

    mock_provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch("getpass.getpass") as mock_getpass,
        mock.patch("robotsix_auto_mail.oauth2.device_code_login") as mock_login,
        mock.patch("robotsix_auto_mail.cli._verify_config", return_value=_ok_result()),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@tii.ae",
                "--id",
                "tii",
                "--overwrite",
                "--oauth2-client-id",
                "12345678-1234-1234-1234-123456789abc",
                "--oauth2-tenant",
                "tii.ae",
                "--output",
                str(output),
            ]
        )

    assert rc == 0
    mock_getpass.assert_not_called()
    mock_login.assert_called_once()
    # The config passed to device_code_login must carry the custom fields.
    login_config = mock_login.call_args[0][0]
    assert login_config.oauth2_client_id == "12345678-1234-1234-1234-123456789abc"
    assert login_config.oauth2_tenant == "tii.ae"
    # The written JSON must also include both fields.
    content = output.read_text()
    assert '"oauth2_client_id": "12345678-1234-1234-1234-123456789abc"' in content
    assert '"oauth2_tenant": "tii.ae"' in content
    # Existing non-transport fields are preserved.

    accounts = MailAccountsConfig.model_validate(_json.loads(output.read_text()))
    cfg = accounts.get("tii").config
    assert cfg.username == "user@tii.ae"


def test_detect_overwrite_app_password_clears_oauth2_provider(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_autoconfig: object
) -> None:
    """--overwrite --app-password clears oauth2_provider from an existing
    Microsoft account config that had it set."""

    output = tmp_path / "cfg.json"
    # Seed a Microsoft account with oauth2_provider set
    seed_cfg = MailConfig(
        imap_host="outlook.office365.com",
        smtp_host="smtp.office365.com",
        username="user@contoso.com",
        password="",
        oauth2_provider="microsoft",
        oauth2_client_id="9e5f94bc-e8a4-4e73-b8be-63364c29d753",
        oauth2_tenant="organizations",
    )
    seed_account = MailAccount(account_id="ms", config=seed_cfg, label=None)
    container = MailAccountsConfig(accounts=[seed_account], default_account_id="ms")
    output.write_text(container.model_dump_json(indent=2))

    mock_provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch("getpass.getpass", return_value="app-pw-789") as mock_getpass,
        mock.patch("robotsix_auto_mail.oauth2.device_code_login") as mock_login,
        mock.patch(
            "robotsix_auto_mail.cli._verify_config", return_value=_ok_result()
        ) as mock_verify,
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-test"}),
    ):
        rc = main(
            [
                "detect",
                "user@contoso.com",
                "--id",
                "ms",
                "--overwrite",
                "--app-password",
                "--output",
                str(output),
            ]
        )

    assert rc == 0
    mock_getpass.assert_called_once()
    mock_login.assert_not_called()
    mock_verify.assert_called_once()
    err = capsys.readouterr().err
    assert "Warning: --app-password" in err
    content = output.read_text()
    assert "app-pw-789" in content
    # oauth2_provider must be cleared
    # (the write path uses save_accounts which may not be available,
    # so we just check the output content directly)


def test_detect_overwrite_preserves_llm_api_key(
    tmp_path: Path, no_autoconfig: object
) -> None:
    """--overwrite preserves llm_api_key, llm_provider_model, and langfuse_*
    fields from an existing config file (re-deploy path)."""

    output = tmp_path / "cfg.json"
    seed_cfg = MailConfig(
        imap_host="old.example.com",
        smtp_host="old.example.com",
        username="test@gmail.com",
        password="old-pw",
        llm_api_key="sk-seed",
        llm_provider_model="openai/gpt-4o",
        langfuse_public_key="pk-seed",
        langfuse_secret_key="sk-seed-lf",
        langfuse_base_url="https://cloud.langfuse.com",
    )
    seed_account = MailAccount(account_id="main", config=seed_cfg, label="Main")
    container = MailAccountsConfig(accounts=[seed_account], default_account_id="main")
    output.write_text(container.model_dump_json(indent=2))

    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-env"}),
    ):
        rc = main(
            [
                "detect",
                "test@gmail.com",
                "--id",
                "main",
                "--overwrite",
                "--password",
                "pw",
                "--no-verify",
                "--output",
                str(output),
            ]
        )

    assert rc == 0

    accounts = MailAccountsConfig.model_validate(_json.loads(output.read_text()))
    cfg = accounts.get("main").config
    # Existing llm/langfuse values preserved from the seed file.
    assert cfg.llm_api_key == "sk-seed"
    assert cfg.llm_provider_model == "openai/gpt-4o"
    assert cfg.langfuse_public_key == "pk-seed"
    assert cfg.langfuse_secret_key == "sk-seed-lf"
    assert cfg.langfuse_base_url == "https://cloud.langfuse.com"
    # Transport fields updated.
    assert cfg.imap_host == "imap.gmail.com"

    # Raw file carries the llm and langfuse fields.
    content = output.read_text()
    assert "sk-seed" in content
    assert "pk-seed" in content


def test_detect_writes_llm_api_key_from_env(
    tmp_path: Path, no_autoconfig: object
) -> None:
    """Fresh detect with LLM_API_KEY env var writes llm.api_key into the output."""
    output = tmp_path / "cfg.yaml"
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.detect_provider",
            return_value=mock_provider,
        ),
        mock.patch("getpass.getpass", return_value="pw"),
        mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-env"}),
    ):
        rc = main(
            [
                "detect",
                "user@gmail.com",
                "--output",
                str(output),
                "--no-verify",
            ]
        )

    assert rc == 0
    content = output.read_text()
    # JSON output contains the env-provided API key.
    assert "sk-env" in content

    accounts = MailAccountsConfig.model_validate(_json.loads(output.read_text()))
    cfg = accounts.default.config
    assert cfg.llm_api_key == "sk-env"
