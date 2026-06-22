"""Tests for propose_archive_subfolder name construction."""

from __future__ import annotations

from tests.conftest import _make_record

from robotsix_auto_mail.triage import propose_archive_subfolder


def test_propose_mailing_list_prefix() -> None:
    """[python-dev] → Lists/python-dev."""
    record = _make_record(
        message_id="<a>",
        sender="a@b.com",
        subject="[python-dev] Re: some topic",
        date="2025-06-01T12:00:00",
    )
    assert propose_archive_subfolder(record) == "Lists/python-dev"


def test_propose_mailing_list_with_post_id() -> None:
    """[list:123] → Lists/list."""
    record = _make_record(
        message_id="<a>",
        sender="a@b.com",
        subject="[list:123] Re: something",
        date="2025-06-01T12:00:00",
    )
    assert propose_archive_subfolder(record) == "Lists/list"


def test_propose_mailing_list_with_space_and_id() -> None:
    """[list 456] → Lists/list."""
    record = _make_record(
        message_id="<a>",
        sender="a@b.com",
        subject="[list 456] hello",
        date="2025-06-01T12:00:00",
    )
    assert propose_archive_subfolder(record) == "Lists/list"


def test_propose_empty_brackets_skipped() -> None:
    """[] should fall through to root (no rules match)."""
    record = _make_record(
        message_id="<a>",
        sender="alice@example.com",
        subject="[] Re: something",
        date="2025-06-01T12:00:00",
    )
    result = propose_archive_subfolder(record)
    # Falls through to root — domain/sender rule removed.
    assert result == ""


def test_propose_sender_domain_and_local_part() -> None:
    """alice@example.com → '' (domain/sender rule removed)."""
    record = _make_record(
        message_id="<a>",
        sender="Alice <alice@example.com>",
        subject="Hello",
        date="2025-06-01T12:00:00",
    )
    assert propose_archive_subfolder(record) == ""


def test_propose_bare_sender_no_brackets() -> None:
    """bob@example.com → '' (domain/sender rule removed)."""
    record = _make_record(
        message_id="<a>",
        sender="bob@example.com",
        subject="Hi",
        date="2025-06-01T12:00:00",
    )
    assert propose_archive_subfolder(record) == ""


def test_propose_sender_no_at_falls_through() -> None:
    """Sender with no @ falls through to root (date rule removed)."""
    record = _make_record(
        message_id="<a>",
        sender="NoEmailName",
        subject="Hi",
        date="2025-06-15T12:00:00",
    )
    assert propose_archive_subfolder(record) == ""


def test_propose_date_iso() -> None:
    """ISO date → '' (date rule removed)."""
    record = _make_record(
        message_id="<a>",
        sender="NoEmail",
        subject="No list",
        date="2025-06-01T12:00:00",
    )
    assert propose_archive_subfolder(record) == ""


def test_propose_unparseable_date_returns_unknown() -> None:
    """Unparseable date → '' (date rule removed)."""
    record = _make_record(
        message_id="<a>",
        sender="NoEmail",
        subject="No list",
        date="not-a-date",
    )
    assert propose_archive_subfolder(record) == ""


def test_propose_all_rules_fail_returns_empty() -> None:
    """If ALL rules fail (impossible with date fallback? only if date is
    empty and no sender/@domain). Actually date fallback catches empty
    strings too via 'unknown'."""
    record = _make_record(
        message_id="<a>",
        sender="noemail",
        subject="no list",
        date="",
    )
    # Date is empty → all-rules-fail → ""
    assert propose_archive_subfolder(record) == ""


def test_propose_sanitises_special_chars() -> None:
    """Non-alphanumeric chars collapsed to single dash, lowercased."""
    record = _make_record(
        message_id="<a>",
        sender="Alice+Tag <alice+tag@example.com>",
        subject="[My List!!!] Re: topic",
        date="2025-06-01T12:00:00",
    )
    # List rule wins: "My List!!!" → sanitised to "my-list"
    assert propose_archive_subfolder(record) == "Lists/my-list"
