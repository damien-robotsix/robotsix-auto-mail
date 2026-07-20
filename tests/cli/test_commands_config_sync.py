"""Direct unit tests for ``_cmd_config_sync`` and ``_cmd_config_sync_set``.

These import the handler functions directly (not through ``main()``) so
every code path — including the internal ``ImportError`` branches — can
be exercised without going through argument-parsing dispatch.
"""

from __future__ import annotations

import argparse
import json
import sys
from unittest import mock

import pytest

from robotsix_auto_mail.cli.commands_config_sync import (
    _cmd_config_sync,
    _cmd_config_sync_set,
)
from robotsix_auto_mail.config.config_sync_agent import (
    ConfigSyncError,
    ConfigSyncResult,
    DriftProposal,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sync_args(**overrides: object) -> argparse.Namespace:
    """Build an ``argparse.Namespace`` with defaults for ``_cmd_config_sync``."""
    defaults: dict[str, object] = dict(
        account=None,
        api_key=None,
        provider_model=None,
        output_format="text",
        dedup=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _make_sync_set_args(**overrides: object) -> argparse.Namespace:
    """Build an ``argparse.Namespace`` with defaults for ``_cmd_config_sync_set``."""
    defaults: dict[str, object] = dict(
        account=None,
        fingerprint="abc123",
        state="accepted",
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# _cmd_config_sync
# ---------------------------------------------------------------------------


class TestCmdConfigSync:
    """Direct unit tests for ``_cmd_config_sync``."""

    def test_text_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Happy path — proposals rendered as text, rc=0."""
        result = ConfigSyncResult(
            proposals=[
                DriftProposal(
                    title="Test drift",
                    body="Something is off.",
                    affected_field="test_field",
                    confidence="high",
                )
            ]
        )
        with mock.patch(
            "robotsix_auto_mail.config.config_sync_agent.run_config_sync_agent",
            return_value=result,
        ):
            rc = _cmd_config_sync(_make_sync_args())

        assert rc == 0
        out = capsys.readouterr().out
        assert "Config Drift Advisory" in out
        assert "Test drift" in out
        assert "Something is off." in out
        assert "test_field" in out

    def test_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """``--output-format json`` prints parseable JSON, rc=0."""
        result = ConfigSyncResult(
            proposals=[
                DriftProposal(
                    title="JSON drift",
                    body="Body text.",
                    affected_field="json_field",
                    confidence="low",
                )
            ]
        )
        with mock.patch(
            "robotsix_auto_mail.config.config_sync_agent.run_config_sync_agent",
            return_value=result,
        ):
            rc = _cmd_config_sync(_make_sync_args(output_format="json"))

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["proposals"][0]["title"] == "JSON drift"
        assert payload["proposals"][0]["affected_field"] == "json_field"

    def test_no_drift(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Empty proposals → 'No config drift detected.', rc=0."""
        with mock.patch(
            "robotsix_auto_mail.config.config_sync_agent.run_config_sync_agent",
            return_value=ConfigSyncResult(proposals=[]),
        ):
            rc = _cmd_config_sync(_make_sync_args())

        assert rc == 0
        assert "No config drift detected." in capsys.readouterr().out

    def test_import_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Missing pydantic-ai → ImportError → exit 1 with hint."""
        with mock.patch.dict(
            sys.modules,
            {"robotsix_auto_mail.config.config_sync_agent": None},
        ):
            rc = _cmd_config_sync(_make_sync_args())

        assert rc == 1
        err = capsys.readouterr().err
        assert "pydantic-ai" in err

    def test_config_sync_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """``ConfigSyncError`` from the agent → exit 1, error on stderr."""
        with mock.patch(
            "robotsix_auto_mail.config.config_sync_agent.run_config_sync_agent",
            side_effect=ConfigSyncError("surface read failed"),
        ):
            rc = _cmd_config_sync(_make_sync_args())

        assert rc == 1
        err = capsys.readouterr().err
        assert "Error:" in err
        assert "surface read failed" in err

    def test_dedup_forwards_conn_to_agent(self, tmp_path) -> None:
        """``--dedup`` calls ``init_db`` and forwards the connection."""
        from robotsix_auto_mail.config import MailConfig

        db_path = str(tmp_path / "ledger.db")
        cfg = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
            password="s3cret",
            db_path=db_path,
        )
        with (
            mock.patch(
                "robotsix_auto_mail.config.config_sync_agent.run_config_sync_agent",
                return_value=ConfigSyncResult(proposals=[]),
            ) as mock_agent,
            mock.patch(
                "robotsix_auto_mail.cli.commands_config_sync._load_config_or_exit",
                return_value=cfg,
            ),
            mock.patch(
                "robotsix_auto_mail.cli.commands_config_sync.init_db",
            ) as mock_init_db,
        ):
            rc = _cmd_config_sync(_make_sync_args(dedup=True))

        assert rc == 0
        mock_init_db.assert_called_once_with(db_path)
        mock_agent.assert_called_once()
        assert mock_agent.call_args.kwargs["conn"] is mock_init_db.return_value


# ---------------------------------------------------------------------------
# _cmd_config_sync_set
# ---------------------------------------------------------------------------


class TestCmdConfigSyncSet:
    """Direct unit tests for ``_cmd_config_sync_set``."""

    def test_import_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Missing pydantic-ai → ImportError → exit 1 with hint."""
        with mock.patch.dict(
            sys.modules,
            {"robotsix_auto_mail.config.config_sync_agent": None},
        ):
            rc = _cmd_config_sync_set(_make_sync_set_args())

        assert rc == 1
        err = capsys.readouterr().err
        assert "pydantic-ai" in err

    def test_invalid_state(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Invalid state string → exit 1 with clear error message."""
        rc = _cmd_config_sync_set(_make_sync_set_args(state="banana"))

        assert rc == 1
        err = capsys.readouterr().err
        assert "invalid state" in err
        assert "banana" in err

    def test_success(self, tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
        """Valid state + known fingerprint → success message, exit 0."""
        from robotsix_auto_mail.config import MailConfig

        db_path = str(tmp_path / "ledger.db")
        cfg = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
            password="s3cret",
            db_path=db_path,
        )
        with (
            mock.patch(
                "robotsix_auto_mail.cli.commands_config_sync._load_config_or_exit",
                return_value=cfg,
            ),
            mock.patch(
                "robotsix_auto_mail.cli.commands_config_sync.init_db",
            ) as mock_init_db,
            mock.patch(
                "robotsix_auto_mail.config.config_sync_agent.set_finding_state",
            ) as mock_set,
        ):
            rc = _cmd_config_sync_set(_make_sync_set_args())

        assert rc == 0
        out = capsys.readouterr().out
        assert "Recorded config-drift finding state" in out
        assert "abc123" in out
        assert "accepted" in out
        mock_init_db.assert_called_once_with(db_path)
        mock_set.assert_called_once_with(
            mock_init_db.return_value, "abc123", "accepted"
        )
        mock_init_db.return_value.close.assert_called_once()

    def test_set_finding_state_error(
        self, tmp_path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``ConfigSyncError`` from ``set_finding_state`` → exit 1."""
        from robotsix_auto_mail.config import MailConfig

        db_path = str(tmp_path / "ledger.db")
        cfg = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
            password="s3cret",
            db_path=db_path,
        )
        with (
            mock.patch(
                "robotsix_auto_mail.cli.commands_config_sync._load_config_or_exit",
                return_value=cfg,
            ),
            mock.patch(
                "robotsix_auto_mail.cli.commands_config_sync.init_db",
            ),
            mock.patch(
                "robotsix_auto_mail.config.config_sync_agent.set_finding_state",
                side_effect=ConfigSyncError("No ledger finding"),
            ),
        ):
            rc = _cmd_config_sync_set(
                _make_sync_set_args(fingerprint="deadbeef")
            )

        assert rc == 1
        err = capsys.readouterr().err
        assert "Error:" in err
        assert "No ledger finding" in err
