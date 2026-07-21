"""Unit tests for ``robotsix_auto_mail.cli.commands_detect`` — _cmd_detect handler.

Tests the _cmd_detect orchestration function via mocked callbacks.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.cli.commands_detect import _cmd_detect
from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.config.detect import MailProvider

# ---------------------------------------------------------------------------
# _cmd_detect — unit tests via mocked callbacks
# ---------------------------------------------------------------------------


def _default_config() -> MailConfig:
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        imap_port=993,
        imap_tls_mode="direct-tls",
        smtp_port=587,
        smtp_tls_mode="starttls",
    )


def _mock_detect_imports() -> mock._patch | None:
    """Ensure the lazy pydantic-ai import inside _cmd_detect succeeds."""
    return None  # real import works in test environment


def test_cmd_detect_missing_pydantic_ai() -> None:
    """_cmd_detect exits 1 when pydantic-ai import fails."""
    # Remove the module from sys.modules to force a re-import inside _cmd_detect.
    real_detect = sys.modules.pop("robotsix_auto_mail.config.detect", None)
    original_import = __import__

    def _block_detect(name: str, *args: object, **kwargs: object) -> object:
        if name == "robotsix_auto_mail.config.detect":
            raise ImportError("No module named 'pydantic_ai'")
        return original_import(name, *args, **kwargs)  # type: ignore[arg-type]

    try:
        with mock.patch("builtins.__import__", side_effect=_block_detect):
            args = argparse.Namespace(
                email="user@example.com",
                id=None,
                password=None,
                output="",
                stdout=False,
                overwrite=False,
                no_verify=False,
                app_password=False,
                oauth2_client_id="",
                oauth2_tenant="",
            )
            rc = _cmd_detect(args)
        assert rc == 1
    finally:
        if real_detect is not None:
            sys.modules["robotsix_auto_mail.config.detect"] = real_detect


def test_cmd_detect_app_password_oauth2_mutual_exclusion_client_id() -> None:
    """--app-password + --oauth2-client-id exits 1 before detection runs."""
    args = argparse.Namespace(
        email="user@example.com",
        id=None,
        password=None,
        output="",
        stdout=False,
        overwrite=False,
        no_verify=False,
        app_password=True,
        oauth2_client_id="some-id",
        oauth2_tenant="",
    )
    with mock.patch(
        "robotsix_auto_mail.cli.commands_detect._detect_settings"
    ) as mock_ds:
        rc = _cmd_detect(args)

    assert rc == 1
    mock_ds.assert_not_called()


def test_cmd_detect_app_password_oauth2_mutual_exclusion_tenant() -> None:
    """--app-password + --oauth2-tenant exits 1 before detection runs."""
    args = argparse.Namespace(
        email="user@example.com",
        id=None,
        password=None,
        output="",
        stdout=False,
        overwrite=False,
        no_verify=False,
        app_password=True,
        oauth2_client_id="",
        oauth2_tenant="common",
    )
    with mock.patch(
        "robotsix_auto_mail.cli.commands_detect._detect_settings"
    ) as mock_ds:
        rc = _cmd_detect(args)

    assert rc == 1
    mock_ds.assert_not_called()


def test_cmd_detect_provider_none() -> None:
    """When _detect_settings returns None, _cmd_detect exits 1."""
    mock_provider = MailProvider(imap_host="imap.llm.com", smtp_host="smtp.llm.com")

    with (
        mock.patch(
            "robotsix_auto_mail.config.detect.is_microsoft_provider",
            return_value=False,
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._detect_settings",
            return_value=(mock_provider, []),
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._get_password",
            return_value="pw",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._verify_and_refine",
            return_value=(0, _default_config()),
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._probe_capabilities",
            return_value=(["IMAP4rev1"], {}),
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._print_detect_report",
        ),
    ):
        # Now test when _detect_settings returns (None, [])
        with mock.patch(
            "robotsix_auto_mail.cli.commands_detect._detect_settings",
            return_value=(None, []),
        ):
            args = argparse.Namespace(
                email="user@example.com",
                id=None,
                password="pw",
                output="",
                stdout=False,
                overwrite=False,
                no_verify=False,
                app_password=False,
                oauth2_client_id="",
                oauth2_tenant="",
            )
            rc = _cmd_detect(args)
        assert rc == 1


def test_cmd_detect_stdout_path() -> None:
    """--stdout prints account config JSON and returns 0."""
    mock_provider = MailProvider(imap_host="imap.mail.com", smtp_host="smtp.mail.com")
    stdout = io.StringIO()
    stderr = io.StringIO()

    with (
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._detect_settings",
            return_value=(mock_provider, []),
        ),
        mock.patch(
            "robotsix_auto_mail.config.detect.is_microsoft_provider",
            return_value=False,
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._get_password",
            return_value="pw",
        ),
        mock.patch("sys.stdout", stdout),
        mock.patch("sys.stderr", stderr),
    ):
        args = argparse.Namespace(
            email="user@example.com",
            id=None,
            password=None,
            output="",
            stdout=True,
            overwrite=False,
            no_verify=False,
            app_password=False,
            oauth2_client_id="",
            oauth2_tenant="",
        )
        rc = _cmd_detect(args)

    assert rc == 0
    stdout_text = stdout.getvalue()
    parsed = json.loads(stdout_text)
    assert parsed["default_account_id"] is not None
    assert len(parsed["accounts"]) == 1


def test_cmd_detect_stdout_microsoft_app_password_clears_oauth2() -> None:
    """--stdout + microsoft + --app-password clears oauth2_provider in output."""
    mock_provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    with (
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._detect_settings",
            return_value=(mock_provider, []),
        ),
        mock.patch(
            "robotsix_auto_mail.config.detect.is_microsoft_provider",
            return_value=True,
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._get_password",
            return_value="pw",
        ),
        mock.patch("sys.stdout", stdout),
        mock.patch("sys.stderr", stderr),
    ):
        args = argparse.Namespace(
            email="user@contoso.com",
            id=None,
            password=None,
            output="",
            stdout=True,
            overwrite=False,
            no_verify=False,
            app_password=True,
            oauth2_client_id="",
            oauth2_tenant="",
        )
        rc = _cmd_detect(args)

    assert rc == 0
    stdout_text = stdout.getvalue()
    parsed = json.loads(stdout_text)
    account_cfg = parsed["accounts"][0]["config"]
    # oauth2_provider should be cleared by --app-password logic.
    assert account_cfg.get("oauth2_provider", "") == ""


def test_cmd_detect_happy_path_output_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Full happy path: detection → verification → file save → report."""
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")
    output_path = tmp_path / "config.json"
    config = _default_config()

    with (
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._detect_settings",
            return_value=(mock_provider, ["mx1"]),
        ),
        mock.patch(
            "robotsix_auto_mail.config.detect.is_microsoft_provider",
            return_value=False,
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._get_password",
            return_value="s3cret",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._verify_and_refine",
            return_value=(0, config),
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._probe_capabilities",
            return_value=(["IMAP4rev1"], {"STARTTLS": ""}),
        ),
    ):
        args = argparse.Namespace(
            email="user@example.com",
            id="my-acct",
            password=None,
            output=str(output_path),
            stdout=False,
            overwrite=False,
            no_verify=False,
            app_password=False,
            oauth2_client_id="",
            oauth2_tenant="",
        )
        rc = _cmd_detect(args)

    assert rc == 0
    assert output_path.exists()

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["imap_host"] == "imap.example.com"
    assert report["login_ok"] is True


def test_cmd_detect_verify_and_refine_returns_none_config() -> None:
    """When _verify_and_refine returns (rc, None), exit with rc."""
    mock_provider = MailProvider(imap_host="imap.llm.com", smtp_host="smtp.llm.com")

    with (
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._detect_settings",
            return_value=(mock_provider, []),
        ),
        mock.patch(
            "robotsix_auto_mail.config.detect.is_microsoft_provider",
            return_value=False,
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._get_password",
            return_value="pw",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._verify_and_refine",
            return_value=(1, None),
        ),
    ):
        args = argparse.Namespace(
            email="user@example.com",
            id=None,
            password="pw",
            output="",
            stdout=False,
            overwrite=False,
            no_verify=False,
            app_password=False,
            oauth2_client_id="",
            oauth2_tenant="",
        )
        rc = _cmd_detect(args)

    assert rc == 1


def test_cmd_detect_account_exists_no_overwrite(tmp_path: Path) -> None:
    """When an account with the same id already exists and --overwrite is not set,
    exit 1 and do not mutate the file."""
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")
    output_path = tmp_path / "existing.json"
    existing_config = MailConfig(
        imap_host="old.host.com",
        smtp_host="old.smtp.com",
        username="user@example.com",
        password="oldpass",
    )
    existing_account = MailAccount(
        account_id="my-acct", config=existing_config, label="Old Label"
    )
    existing = MailAccountsConfig(
        accounts=[existing_account],
        default_account_id="my-acct",
    )
    output_path.write_text(existing.model_dump_json())

    config = _default_config()

    with (
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._detect_settings",
            return_value=(mock_provider, []),
        ),
        mock.patch(
            "robotsix_auto_mail.config.detect.is_microsoft_provider",
            return_value=False,
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._get_password",
            return_value="s3cret",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._verify_and_refine",
            return_value=(0, config),
        ),
    ):
        args = argparse.Namespace(
            email="user@example.com",
            id="my-acct",
            password=None,
            output=str(output_path),
            stdout=False,
            overwrite=False,
            no_verify=False,
            app_password=False,
            oauth2_client_id="",
            oauth2_tenant="",
        )
        rc = _cmd_detect(args)

    assert rc == 1
    # The existing file should be untouched.
    reloaded = MailAccountsConfig.model_validate(json.loads(output_path.read_text()))
    assert reloaded.accounts[0].config.imap_host == "old.host.com"


def test_cmd_detect_account_exists_overwrite_merge(tmp_path: Path) -> None:
    """--overwrite merges transport fields into existing account, preserving
    non-transport fields and label."""
    mock_provider = MailProvider(imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")
    output_path = tmp_path / "existing.json"
    existing_config = MailConfig(
        imap_host="old.host.com",
        smtp_host="old.smtp.com",
        username="user@example.com",
        password="oldpass",
        imap_port=993,
        imap_tls_mode="direct-tls",
        smtp_port=587,
        smtp_tls_mode="starttls",
        db_path="/custom/db/path.db",
        archive_root="/custom/archive",
        oauth2_provider="",
        oauth2_client_id="",
        oauth2_tenant="organizations",
    )
    existing_account = MailAccount(
        account_id="my-acct", config=existing_config, label="Preserved Label"
    )
    existing = MailAccountsConfig(
        accounts=[existing_account],
        default_account_id="my-acct",
    )
    output_path.write_text(existing.model_dump_json())

    new_config = MailConfig(
        imap_host="imap.new.com",
        smtp_host="smtp.new.com",
        username="user@example.com",
        password="newpass",
        imap_port=993,
        imap_tls_mode="direct-tls",
        smtp_port=587,
        smtp_tls_mode="starttls",
        oauth2_provider="microsoft",
        oauth2_client_id="new-client-id",
        oauth2_tenant="common",
    )

    with (
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._detect_settings",
            return_value=(mock_provider, []),
        ),
        mock.patch(
            "robotsix_auto_mail.config.detect.is_microsoft_provider",
            return_value=False,
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._get_password",
            return_value="s3cret",
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._verify_and_refine",
            return_value=(0, new_config),
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._probe_capabilities",
            return_value=(["IMAP4rev1"], {}),
        ),
    ):
        args = argparse.Namespace(
            email="user@example.com",
            id="my-acct",
            password=None,
            output=str(output_path),
            stdout=False,
            overwrite=True,
            no_verify=False,
            app_password=False,
            oauth2_client_id="",
            oauth2_tenant="",
        )
        rc = _cmd_detect(args)

    assert rc == 0

    # Reload and verify merge.
    reloaded = MailAccountsConfig.model_validate(json.loads(output_path.read_text()))
    merged = reloaded.accounts[0]
    assert merged.label == "Preserved Label"
    merged_cfg = merged.config
    # Transport fields are updated.
    assert merged_cfg.imap_host == "imap.new.com"
    assert merged_cfg.smtp_host == "smtp.new.com"
    assert merged_cfg.password.get_secret_value() == "newpass"
    # Non-transport fields preserved.
    assert merged_cfg.db_path == "/custom/db/path.db"
    assert merged_cfg.archive_root == "/custom/archive"


def test_cmd_detect_microsoft_account_no_password_prompt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Microsoft accounts skip password prompting entirely (no _get_password call)."""
    mock_provider = MailProvider(
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com"
    )

    with (
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._detect_settings",
            return_value=(mock_provider, []),
        ),
        mock.patch(
            "robotsix_auto_mail.config.detect.is_microsoft_provider",
            return_value=True,
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._get_password",
        ) as mock_getpass,
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._verify_and_refine",
            return_value=(0, _default_config()),
        ),
        mock.patch(
            "robotsix_auto_mail.cli.commands_detect._probe_capabilities",
            return_value=([], {}),
        ),
    ):
        args = argparse.Namespace(
            email="user@contoso.com",
            id=None,
            password=None,
            output="",
            stdout=False,
            overwrite=False,
            no_verify=False,
            app_password=False,
            oauth2_client_id="",
            oauth2_tenant="",
        )
        rc = _cmd_detect(args)

    assert rc == 0
    mock_getpass.assert_not_called()
