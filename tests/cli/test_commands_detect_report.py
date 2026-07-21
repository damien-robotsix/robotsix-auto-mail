"""Unit tests for ``robotsix_auto_mail.cli.commands_detect`` — report handlers.

Tests _build_detect_report and _print_detect_report directly.
"""

from __future__ import annotations

import json

import pytest

from robotsix_auto_mail.cli.commands_detect import (
    _build_detect_report,
    _print_detect_report,
)

# ---------------------------------------------------------------------------
# _build_detect_report — pure unit tests (no mocking)
# ---------------------------------------------------------------------------


def test_build_detect_report_basic() -> None:
    """Report includes all required top-level keys."""
    report = _build_detect_report(
        imap_host="imap.example.com",
        imap_port=993,
        imap_tls_mode="SSL",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_tls_mode="STARTTLS",
        username="user@example.com",
        oauth2_client_id="",
        oauth2_tenant="",
        oauth2_provider="",
        verified=True,
        imap_capabilities=["IMAP4rev1", "IDLE"],
        smtp_features={"SIZE": "10240000"},
    )
    assert report["imap_host"] == "imap.example.com"
    assert report["imap_port"] == 993
    assert report["imap_tls_mode"] == "SSL"
    assert report["smtp_host"] == "smtp.example.com"
    assert report["smtp_port"] == 587
    assert report["smtp_tls_mode"] == "STARTTLS"
    assert report["username"] == "user@example.com"
    assert report["imap_capabilities"] == ["IMAP4rev1", "IDLE"]
    assert report["smtp_features"] == {"SIZE": "10240000"}
    assert report["login_ok"] is True
    # Password must never appear in report.
    assert "password" not in report


def test_build_detect_report_oauth2_fields_present() -> None:
    """When oauth2 fields are non-empty they appear in the report."""
    report = _build_detect_report(
        imap_host="outlook.office365.com",
        imap_port=993,
        imap_tls_mode="SSL",
        smtp_host="smtp.office365.com",
        smtp_port=587,
        smtp_tls_mode="STARTTLS",
        username="user@contoso.com",
        oauth2_client_id="some-client-id",
        oauth2_tenant="organizations",
        oauth2_provider="microsoft",
        verified=True,
        imap_capabilities=["IMAP4rev1", "AUTH=XOAUTH2"],
        smtp_features={"AUTH": "XOAUTH2"},
    )
    assert report["oauth2_client_id"] == "some-client-id"
    assert report["oauth2_tenant"] == "organizations"
    assert report["oauth2_provider"] == "microsoft"


def test_build_detect_report_oauth2_fields_absent() -> None:
    """When oauth2 fields are empty they are omitted from the report."""
    report = _build_detect_report(
        imap_host="imap.gmail.com",
        imap_port=993,
        imap_tls_mode="SSL",
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_tls_mode="STARTTLS",
        username="user@gmail.com",
        oauth2_client_id="",
        oauth2_tenant="",
        oauth2_provider="",
        verified=False,
        imap_capabilities=[],
        smtp_features={},
    )
    assert "oauth2_client_id" not in report
    assert "oauth2_tenant" not in report
    assert "oauth2_provider" not in report


def test_build_detect_report_verified_false() -> None:
    """login_ok is False when verified is False."""
    report = _build_detect_report(
        imap_host="imap.example.com",
        imap_port=993,
        imap_tls_mode="SSL",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_tls_mode="STARTTLS",
        username="user@example.com",
        oauth2_client_id="",
        oauth2_tenant="",
        oauth2_provider="",
        verified=False,
        imap_capabilities=[],
        smtp_features={},
    )
    assert report["login_ok"] is False


def test_build_detect_report_empty_capabilities() -> None:
    """Empty capabilities/features produce empty lists/dicts."""
    report = _build_detect_report(
        imap_host="h",
        imap_port=1,
        imap_tls_mode="NONE",
        smtp_host="s",
        smtp_port=1,
        smtp_tls_mode="NONE",
        username="u",
        oauth2_client_id="",
        oauth2_tenant="",
        oauth2_provider="",
        verified=True,
        imap_capabilities=[],
        smtp_features={},
    )
    assert report["imap_capabilities"] == []
    assert report["smtp_features"] == {}


# ---------------------------------------------------------------------------
# _print_detect_report — capsys tests
# ---------------------------------------------------------------------------


def test_print_detect_report_writes_json_to_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_print_detect_report writes valid JSON to stdout and instructions to stderr."""
    report: dict[str, object] = {
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "imap_tls_mode": "SSL",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_tls_mode": "STARTTLS",
        "username": "user@example.com",
        "imap_capabilities": ["IMAP4rev1"],
        "smtp_features": {},
        "login_ok": True,
    }
    _print_detect_report(report)

    captured = capsys.readouterr()
    stdout = captured.out
    stderr = captured.err

    # Valid JSON on stdout.
    parsed = json.loads(stdout)
    assert parsed["imap_host"] == "imap.example.com"
    assert parsed["username"] == "user@example.com"

    # Instructions on stderr.
    assert "Copy the settings above" in stderr


def test_print_detect_report_no_extra_keys(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_print_detect_report outputs exactly the keys in the report dict."""
    report: dict[str, object] = {"a": 1, "b": "two"}
    _print_detect_report(report)

    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed == {"a": 1, "b": "two"}
