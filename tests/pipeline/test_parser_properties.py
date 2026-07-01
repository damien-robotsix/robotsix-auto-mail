"""Property-based (Hypothesis) tests for the MIME parser.

Example-based tests in ``test_parser.py`` cover concrete cases; these
tests exercise the unbounded MIME input space to give confidence in the
documented invariants — most importantly that ``parse_message`` never
crashes on arbitrary bytes (it either returns a ``MailRecord`` or raises
``ParseError``).
"""

from __future__ import annotations

import email.message
import email.policy
import json

import pytest

pytest.importorskip("hypothesis")

from hypothesis import assume, given
from hypothesis import strategies as st

from robotsix_auto_mail.pipeline._parse import ParseError, parse_message

# ---------------------------------------------------------------------------
# Never-crashes invariant
# ---------------------------------------------------------------------------


@pytest.mark.slow
@given(st.binary(max_size=4096))
def test_parse_never_crashes(data: bytes) -> None:
    """Arbitrary bytes either parse or raise ParseError — nothing else.

    Any non-``ParseError`` exception propagates and fails the test (no
    bare ``except``), surfacing a genuine parser bug rather than hiding it.
    """
    try:
        parse_message(data)
    except ParseError:
        pass


# ---------------------------------------------------------------------------
# Structural invariants on success
# ---------------------------------------------------------------------------


@given(st.binary(max_size=4096))
def test_structural_invariants_on_success(data: bytes) -> None:
    try:
        record = parse_message(data)
    except ParseError:
        assume(False)
        return

    assert isinstance(record.message_id, str)
    assert record.message_id != ""

    recipients = json.loads(record.recipients_json)
    assert isinstance(recipients, dict)
    assert isinstance(recipients["to"], list)
    assert isinstance(recipients["cc"], list)

    attachments = json.loads(record.attachments_json)
    assert isinstance(attachments, list)

    assert isinstance(record.body_plain, str)
    assert isinstance(record.body_html, str)


# ---------------------------------------------------------------------------
# Deterministic surrogate / message_id
# ---------------------------------------------------------------------------


@given(st.binary())
def test_message_id_deterministic(data: bytes) -> None:
    """Parsing the same bytes twice yields the same message_id."""
    try:
        first = parse_message(data)
    except ParseError:
        # If it fails once it must fail consistently.
        with pytest.raises(ParseError):
            parse_message(data)
        return

    second = parse_message(data)
    assert first.message_id == second.message_id


# ---------------------------------------------------------------------------
# Round-trip from generated messages
# ---------------------------------------------------------------------------

_addresses = st.from_regex(
    r"\A[a-z][a-z0-9]{0,15}@[a-z][a-z0-9]{0,15}\.(com|org|net)\Z",
    fullmatch=True,
)
_subjects = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    max_size=80,
)
_bodies = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    max_size=200,
)


@given(
    subject=_subjects,
    sender=_addresses,
    to_addrs=st.sets(_addresses, max_size=4),
    cc_addrs=st.sets(_addresses, max_size=4),
    body=_bodies,
    n_attachments=st.integers(min_value=0, max_value=3),
)
def test_round_trip_generated_message(
    subject: str,
    sender: str,
    to_addrs: set[str],
    cc_addrs: set[str],
    body: str,
    n_attachments: int,
) -> None:
    msg = email.message.EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    if to_addrs:
        msg["To"] = ", ".join(sorted(to_addrs))
    if cc_addrs:
        msg["Cc"] = ", ".join(sorted(cc_addrs))
    msg["Message-ID"] = "<roundtrip@example.com>"
    msg.set_content(body)

    for i in range(n_attachments):
        msg.add_attachment(
            b"x" * (i + 1),
            maintype="application",
            subtype="octet-stream",
            filename=f"file{i}.bin",
        )

    record = parse_message(msg.as_bytes())

    assert record.message_id == "<roundtrip@example.com>"
    # ``sender`` is the raw From header value — generated address is a substring.
    assert sender in record.sender

    recipients = json.loads(record.recipients_json)
    assert set(recipients["to"]) == to_addrs
    assert set(recipients["cc"]) == cc_addrs

    attachments = json.loads(record.attachments_json)
    assert len(attachments) == n_attachments
