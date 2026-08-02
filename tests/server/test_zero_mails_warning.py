"""Unit tests for ``_zero_mails_warning_html`` regression guard banner."""

from __future__ import annotations


# ---------------------------------------------------------------------------
# _zero_mails_warning_html
# ---------------------------------------------------------------------------


def test_warning_when_zero_mails_with_accounts() -> None:
    """Returns the warning banner HTML when ``total_mail_count == 0`` and
    ``account_count > 0``."""
    from robotsix_auto_mail.server.views.board import _zero_mails_warning_html

    result = _zero_mails_warning_html(total_mail_count=0, account_count=2)
    assert result != ""
    assert "zero-mails-banner" in result
    assert "No mail fetched yet" in result
    assert "2 account(s) configured" in result
    assert 'role="alert"' in result


def test_no_warning_when_mails_present() -> None:
    """Returns an empty string when ``total_mail_count > 0`` regardless of
    account count."""
    from robotsix_auto_mail.server.views.board import _zero_mails_warning_html

    assert _zero_mails_warning_html(total_mail_count=5, account_count=2) == ""
    assert _zero_mails_warning_html(total_mail_count=1, account_count=1) == ""


def test_no_warning_when_no_accounts() -> None:
    """Returns an empty string when ``account_count == 0`` regardless of
    mail count."""
    from robotsix_auto_mail.server.views.board import _zero_mails_warning_html

    assert _zero_mails_warning_html(total_mail_count=0, account_count=0) == ""
    assert _zero_mails_warning_html(total_mail_count=10, account_count=0) == ""


def test_warning_links_to_probe_health() -> None:
    """The warning banner includes a link to the probe-health endpoint."""
    from robotsix_auto_mail.server.views.board import _zero_mails_warning_html

    result = _zero_mails_warning_html(total_mail_count=0, account_count=1)
    assert 'href="/probe-health"' in result
    assert "recheck connections" in result
