"""Tests for calendar dispatch via TO_CALENDAR column move and dispatch_calendar_request."""

from __future__ import annotations

import pathlib
import ssl
import sys
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from tests.server.conftest import (
    _populate_db,
    _post_form,
    _start_test_server,
    _triage_action,
)

from robotsix_auto_mail.calendar import (
    CalendarDispatchError,
    CalendarEventRequest,
    CalendarEventResponse,
    extract_calendar_summary,
    extract_dates_from_body,
)

if TYPE_CHECKING:
    from robotsix_auto_mail.config.model import MailConfig

# ---------------------------------------------------------------------------
# Helpers — inject/remove fake agent-comm modules
# ---------------------------------------------------------------------------


def _install_fake_agent_comm_modules(
    *,
    send_request_side_effect: object = None,
    reply_body: object = None,
) -> dict[str, mock.MagicMock]:
    """Install synthetic ``robotsix_agent_comm.*`` modules into ``sys.modules``
    and return mocks keyed by short name.

    The fake ``Agent`` instance's ``send_request`` either returns a reply mock
    carrying *reply_body* or raises *send_request_side_effect*.
    """
    mocks: dict[str, mock.MagicMock] = {}

    # -- Agent mock --
    mock_agent_instance = mock.MagicMock()
    if send_request_side_effect is not None:
        mock_agent_instance.send_request.side_effect = send_request_side_effect
    else:
        reply = mock.MagicMock()
        reply.body = (
            reply_body
            if reply_body is not None
            else {
                "result": {
                    "event": {"uid": "evt-1"},
                    "confirmation_text": "Created event 'Test'",
                },
                "correlation_id": "corr-x",
            }
        )
        mock_agent_instance.send_request.return_value = reply
        mocks["reply"] = reply
    mock_agent_cls = mock.MagicMock(return_value=mock_agent_instance)

    agent_not_found_error = type("AgentNotFoundError", (Exception,), {})
    delivery_error = type("DeliveryError", (Exception,), {})
    transport_error = type("TransportError", (Exception,), {})
    transport_timeout_error = type("TransportTimeoutError", (Exception,), {})
    error_cls = type("Error", (), {})

    transport_mod = mock.MagicMock()
    transport_mod.AgentNotFoundError = agent_not_found_error
    transport_mod.DeliveryError = delivery_error
    transport_mod.TransportError = transport_error
    transport_mod.TransportTimeoutError = transport_timeout_error
    transport_mod.Registry = mock.MagicMock

    sdk_mod = mock.MagicMock()
    sdk_mod.Agent = mock_agent_cls

    brokered_request_mod = mock.MagicMock()
    brokered_request_mod.BrokeredRequester = mock.MagicMock()

    protocol_mod = mock.MagicMock()
    protocol_mod.Error = error_cls

    # Top-level package module (empty, just needs to exist).
    top_mod = mock.MagicMock()

    sys.modules["robotsix_agent_comm"] = top_mod
    sys.modules["robotsix_agent_comm.sdk"] = sdk_mod
    sys.modules["robotsix_agent_comm.sdk.brokered_request"] = brokered_request_mod
    sys.modules["robotsix_agent_comm.protocol"] = protocol_mod
    sys.modules["robotsix_agent_comm.transport"] = transport_mod

    mocks["agent_instance"] = mock_agent_instance
    mocks["agent_cls"] = mock_agent_cls
    mocks["transport"] = transport_mod
    mocks["sdk"] = sdk_mod
    mocks["brokered_request_mod"] = brokered_request_mod
    mocks["AgentNotFoundError"] = agent_not_found_error
    mocks["DeliveryError"] = delivery_error
    mocks["TransportError"] = transport_error
    mocks["TransportTimeoutError"] = transport_timeout_error
    mocks["Error"] = error_cls
    return mocks


def _remove_fake_agent_comm_modules() -> None:
    for key in list(sys.modules):
        if key.startswith("robotsix_agent_comm"):
            del sys.modules[key]


def _broker_config(**overrides: object) -> "MailConfig":
    """Build a ``MailConfig`` suitable for brokered calendar dispatch."""
    from robotsix_auto_mail.config.model import MailConfig

    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="s3cret",
        calendar_transport="brokered",
        calendar_broker_host="broker.example.com",
        calendar_broker_port=8443,
        calendar_broker_token="test-token",
        **overrides,
    )


def _sample_event() -> CalendarEventRequest:
    return CalendarEventRequest(
        message_id="<test@example.com>",
        subject="Test",
        sender="sender@example.com",
        body_text="Body",
        email_date="2025-01-01T00:00:00",
    )


# ---------------------------------------------------------------------------
# Unit tests — dispatch_calendar_request
# ---------------------------------------------------------------------------


def test_dispatch_calendar_request_success() -> None:
    """dispatch_calendar_request sends an add_to_calendar request and returns
    the calendar agent's confirmation reference."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    event = _sample_event()
    mocks = _install_fake_agent_comm_modules()
    try:
        result = dispatch_calendar_request(event)
    finally:
        _remove_fake_agent_comm_modules()

    assert isinstance(result, CalendarEventResponse)
    assert result.event_ref == "Created event 'Test'"
    assert result.status == "success"
    assert result.correlation_id == event.correlation_id
    mocks["agent_instance"].send_request.assert_called_once()
    args, _kwargs = mocks["agent_instance"].send_request.call_args
    assert args[0] == "robotsix-calendar"
    assert args[1] == {"add_to_calendar": event.model_dump()}


def test_dispatch_calendar_request_falls_back_to_event_uid() -> None:
    """With no confirmation_text the event UID is returned as the reference."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    mocks = _install_fake_agent_comm_modules(
        reply_body={"result": {"event": {"uid": "evt-42"}}, "correlation_id": "c"},
    )
    try:
        result = dispatch_calendar_request(_sample_event())
    finally:
        _remove_fake_agent_comm_modules()

    assert isinstance(result, CalendarEventResponse)
    assert result.event_ref == "evt-42"
    assert result.status == "success"
    assert "reply" in mocks


def test_dispatch_calendar_error_reply_raises() -> None:
    """An ``{"error": ...}`` reply maps to CalendarDispatchError."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    _install_fake_agent_comm_modules(
        reply_body={"error": {"code": "missing_dates", "message": "no dates"}},
    )
    try:
        with pytest.raises(
            CalendarDispatchError, match=r"Calendar agent error.*no dates"
        ):
            dispatch_calendar_request(_sample_event())
    finally:
        _remove_fake_agent_comm_modules()


def test_dispatch_malformed_reply_raises() -> None:
    """A reply with neither result nor error maps to CalendarDispatchError."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    _install_fake_agent_comm_modules(reply_body={"unexpected": True})
    try:
        with pytest.raises(CalendarDispatchError, match="malformed"):
            dispatch_calendar_request(_sample_event())
    finally:
        _remove_fake_agent_comm_modules()


def test_dispatch_import_error() -> None:
    """dispatch_calendar_request raises CalendarDispatchError on ImportError."""
    import builtins

    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    event = CalendarEventRequest(
        message_id="<test@example.com>",
        subject="Test",
        sender="sender@example.com",
        body_text="Body",
        email_date="2025-01-01T00:00:00",
    )

    # Remove cached robotix_agent_comm modules and block re-import so the
    # lazy import inside dispatch_calendar_request raises ImportError.
    saved = {}
    for key in list(sys.modules):
        if key.startswith("robotsix_agent_comm"):
            saved[key] = sys.modules.pop(key)

    _orig_import = builtins.__import__

    def _block_agent_comm(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("robotsix_agent_comm"):
            raise ImportError(f"No module named {name!r}")
        return _orig_import(name, *args, **kwargs)

    with mock.patch("builtins.__import__", new=_block_agent_comm):
        try:
            with pytest.raises(
                CalendarDispatchError, match="Agent communication is not available"
            ):
                dispatch_calendar_request(event)
        finally:
            sys.modules.update(saved)


def test_dispatch_agent_not_found_error() -> None:
    """dispatch_calendar_request raises CalendarDispatchError on AgentNotFoundError."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    mocks = _install_fake_agent_comm_modules()
    # Use the AgentNotFoundError class from the fake transport module so
    # it matches the one dispatch_calendar_request imports at runtime.
    agent_not_found_error = mocks["AgentNotFoundError"]
    mocks["agent_instance"].send_request.side_effect = agent_not_found_error(
        "robotsix-calendar"
    )
    try:
        with pytest.raises(
            CalendarDispatchError, match="Calendar agent is not available"
        ):
            dispatch_calendar_request(_sample_event())
    finally:
        _remove_fake_agent_comm_modules()


def test_dispatch_delivery_error() -> None:
    """dispatch_calendar_request raises CalendarDispatchError on DeliveryError."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    mocks = _install_fake_agent_comm_modules()
    delivery_error = mocks["DeliveryError"]
    mocks["agent_instance"].send_request.side_effect = delivery_error("timeout")
    try:
        with pytest.raises(
            CalendarDispatchError, match="Failed to deliver calendar request"
        ):
            dispatch_calendar_request(_sample_event())
    finally:
        _remove_fake_agent_comm_modules()


def test_dispatch_unexpected_error() -> None:
    """dispatch_calendar_request wraps unexpected exceptions."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    _install_fake_agent_comm_modules(
        send_request_side_effect=RuntimeError("boom"),
    )
    try:
        with pytest.raises(
            CalendarDispatchError, match="Failed to deliver calendar request"
        ):
            dispatch_calendar_request(_sample_event())
    finally:
        _remove_fake_agent_comm_modules()


# ---------------------------------------------------------------------------
# Unit tests — dispatch_calendar_request (brokered path)
# ---------------------------------------------------------------------------


def test_dispatch_brokered_success() -> None:
    """Brokered dispatch returns the reply string from BrokeredRequester."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    event = _sample_event()
    config = _broker_config()
    mocks = _install_fake_agent_comm_modules()

    # BrokeredRequester.request() returns a string by contract.
    requester_instance = mocks["brokered_request_mod"].BrokeredRequester.return_value
    requester_instance.request.return_value = "Created event 'Brokered Test'"

    try:
        result = dispatch_calendar_request(event, config=config)
    finally:
        _remove_fake_agent_comm_modules()

    assert isinstance(result, CalendarEventResponse)
    assert result.event_ref == "Created event 'Brokered Test'"
    assert result.status == "success"
    assert result.correlation_id == event.correlation_id

    # Verify BrokeredRequester was constructed with expected args.
    brokered_cls = mocks["brokered_request_mod"].BrokeredRequester
    brokered_cls.assert_called_once()
    _, kwargs = brokered_cls.call_args
    assert kwargs["agent_id"] == "robotsix-auto-mail"
    assert kwargs["target_agent_id"] == "robotsix-calendar"
    assert kwargs["broker_host"] == "broker.example.com"
    assert kwargs["broker_port"] == 8443
    assert kwargs["broker_token"] == "test-token"
    assert kwargs["timeout"] == 60.0
    assert kwargs["default_reply"] == "Event created"

    # Verify request was called with the correct payload.
    requester_instance.request.assert_called_once_with(
        {"add_to_calendar": event.model_dump()},
    )


def test_dispatch_brokered_runtime_error() -> None:
    """Brokered dispatch maps RuntimeError to CalendarDispatchError."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    mocks = _install_fake_agent_comm_modules()
    requester_instance = mocks["brokered_request_mod"].BrokeredRequester.return_value
    requester_instance.request.side_effect = RuntimeError("calendar engine error")

    try:
        with pytest.raises(
            CalendarDispatchError, match=r"Calendar agent error.*calendar engine error"
        ):
            dispatch_calendar_request(_sample_event(), config=_broker_config())
    finally:
        _remove_fake_agent_comm_modules()


def test_dispatch_brokered_agent_not_found() -> None:
    """Brokered dispatch maps AgentNotFoundError to CalendarDispatchError."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    mocks = _install_fake_agent_comm_modules()
    agent_not_found_error = mocks["AgentNotFoundError"]
    requester_instance = mocks["brokered_request_mod"].BrokeredRequester.return_value
    requester_instance.request.side_effect = agent_not_found_error("robotsix-calendar")

    try:
        with pytest.raises(
            CalendarDispatchError, match="Calendar agent is not available"
        ):
            dispatch_calendar_request(_sample_event(), config=_broker_config())
    finally:
        _remove_fake_agent_comm_modules()


def test_dispatch_brokered_delivery_error() -> None:
    """Brokered dispatch maps DeliveryError to CalendarDispatchError."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    mocks = _install_fake_agent_comm_modules()
    delivery_error = mocks["DeliveryError"]
    requester_instance = mocks["brokered_request_mod"].BrokeredRequester.return_value
    requester_instance.request.side_effect = delivery_error("message timeout")

    try:
        with pytest.raises(
            CalendarDispatchError, match="Failed to deliver calendar request"
        ):
            dispatch_calendar_request(_sample_event(), config=_broker_config())
    finally:
        _remove_fake_agent_comm_modules()


def test_dispatch_brokered_transport_error() -> None:
    """Brokered dispatch maps TransportError to CalendarDispatchError."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    mocks = _install_fake_agent_comm_modules()
    transport_error = mocks["TransportError"]
    requester_instance = mocks["brokered_request_mod"].BrokeredRequester.return_value
    requester_instance.request.side_effect = transport_error("connection refused")

    try:
        with pytest.raises(
            CalendarDispatchError, match="Failed to deliver calendar request"
        ):
            dispatch_calendar_request(_sample_event(), config=_broker_config())
    finally:
        _remove_fake_agent_comm_modules()


def test_dispatch_brokered_transport_timeout_error() -> None:
    """Brokered dispatch maps TransportTimeoutError to CalendarDispatchError."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    mocks = _install_fake_agent_comm_modules()
    transport_timeout_error = mocks["TransportTimeoutError"]
    requester_instance = mocks["brokered_request_mod"].BrokeredRequester.return_value
    requester_instance.request.side_effect = transport_timeout_error("timed out")

    try:
        with pytest.raises(
            CalendarDispatchError, match="Failed to deliver calendar request"
        ):
            dispatch_calendar_request(_sample_event(), config=_broker_config())
    finally:
        _remove_fake_agent_comm_modules()


def test_dispatch_brokered_ssl_error() -> None:
    """Brokered dispatch maps ssl.SSLError to CalendarDispatchError."""
    import ssl

    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    mocks = _install_fake_agent_comm_modules()
    requester_instance = mocks["brokered_request_mod"].BrokeredRequester.return_value
    requester_instance.request.side_effect = ssl.SSLError("certificate verify failed")

    try:
        with pytest.raises(
            CalendarDispatchError, match="Calendar broker TLS handshake failed"
        ):
            dispatch_calendar_request(_sample_event(), config=_broker_config())
    finally:
        _remove_fake_agent_comm_modules()


def test_dispatch_brokered_os_error() -> None:
    """Brokered dispatch maps OSError to CalendarDispatchError."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    mocks = _install_fake_agent_comm_modules()
    requester_instance = mocks["brokered_request_mod"].BrokeredRequester.return_value
    requester_instance.request.side_effect = OSError("no route to host")

    try:
        with pytest.raises(CalendarDispatchError, match="Calendar broker unreachable"):
            dispatch_calendar_request(_sample_event(), config=_broker_config())
    finally:
        _remove_fake_agent_comm_modules()


def test_dispatch_brokered_unexpected_error() -> None:
    """Brokered dispatch maps unexpected exceptions to CalendarDispatchError."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    mocks = _install_fake_agent_comm_modules()
    requester_instance = mocks["brokered_request_mod"].BrokeredRequester.return_value
    requester_instance.request.side_effect = ValueError("unexpected failure")

    try:
        with pytest.raises(
            CalendarDispatchError, match="Failed to deliver calendar request"
        ):
            dispatch_calendar_request(_sample_event(), config=_broker_config())
    finally:
        _remove_fake_agent_comm_modules()


def test_dispatch_brokered_import_error() -> None:
    """Brokered dispatch raises CalendarDispatchError on ImportError for
    the brokered_request module."""
    from robotsix_auto_mail.calendar.dispatch import dispatch_calendar_request

    # Install fake modules but then delete the brokered_request module so
    # the import inside _dispatch_via_brokered_requester fails.
    mocks = _install_fake_agent_comm_modules()
    del sys.modules["robotsix_agent_comm.sdk.brokered_request"]

    # Also block re-import.
    import builtins

    _orig_import = builtins.__import__

    def _block_brokered(name: str, *args: object, **kwargs: object) -> object:
        if name == "robotsix_agent_comm.sdk.brokered_request":
            raise ImportError(f"No module named {name!r}")
        return _orig_import(name, *args, **kwargs)

    with mock.patch("builtins.__import__", new=_block_brokered):
        try:
            with pytest.raises(
                CalendarDispatchError, match="Agent communication is not available"
            ):
                dispatch_calendar_request(_sample_event(), config=_broker_config())
        finally:
            # Restore the fake module so _remove_fake_agent_comm_modules
            # can clean up cleanly.
            sys.modules["robotsix_agent_comm.sdk.brokered_request"] = mocks[
                "brokered_request_mod"
            ]

    _remove_fake_agent_comm_modules()


# ---------------------------------------------------------------------------
# Unit tests — _build_broker_ssl_context
# ---------------------------------------------------------------------------


def test_build_broker_ssl_context_no_ca_and_no_client_cert() -> None:
    """Returns None when no CA or client cert is configured."""
    from robotsix_auto_mail.calendar.dispatch import _build_broker_ssl_context

    config = _broker_config()
    result = _build_broker_ssl_context(config)
    assert result is None


def test_build_broker_ssl_context_with_ca(tmp_path: "pathlib.Path") -> None:
    """Returns an SSLContext when a CA cert path is configured."""
    from robotsix_auto_mail.calendar.dispatch import _build_broker_ssl_context

    # Write a minimal PEM CA file (the function doesn't validate the
    # certificate — it just attempts to load it into an SSLContext).
    ca_path = tmp_path / "ca.pem"
    _write_self_signed_cert_pem(ca_path)

    config = _broker_config(
        calendar_broker_tls_ca=str(ca_path),
    )
    result = _build_broker_ssl_context(config)
    assert isinstance(result, ssl.SSLContext)
    # The context should be a client-side TLS context.
    assert result.protocol == ssl.PROTOCOL_TLS_CLIENT


def test_build_broker_ssl_context_with_ca_and_client_cert(
    tmp_path: "pathlib.Path",
) -> None:
    """Returns an SSLContext with client cert chain when both CA and
    client cert are configured."""
    from robotsix_auto_mail.calendar.dispatch import _build_broker_ssl_context

    ca_path = tmp_path / "ca.pem"
    cert_path = tmp_path / "client.pem"
    key_path = tmp_path / "client.key"
    _write_self_signed_cert_pem_with_key(ca_path, cert_path, key_path)

    config = _broker_config(
        calendar_broker_tls_ca=str(ca_path),
        calendar_broker_client_cert=str(cert_path),
        calendar_broker_client_key=str(key_path),
    )
    result = _build_broker_ssl_context(config)
    assert isinstance(result, ssl.SSLContext)
    assert result.protocol == ssl.PROTOCOL_TLS_CLIENT


def test_build_broker_ssl_context_client_cert_only(tmp_path: "pathlib.Path") -> None:
    """Returns an SSLContext with system trust + client cert when only
    client cert is configured (no custom CA)."""
    from robotsix_auto_mail.calendar.dispatch import _build_broker_ssl_context

    cert_path = tmp_path / "client.pem"
    _write_self_signed_cert_pem(cert_path)

    config = _broker_config(
        calendar_broker_client_cert=str(cert_path),
    )
    result = _build_broker_ssl_context(config)
    assert isinstance(result, ssl.SSLContext)
    # When no CA is provided, the base context comes from
    # ssl.create_default_context().
    assert result.protocol == ssl.PROTOCOL_TLS_CLIENT


# -- SSL helpers ----------------------------------------------------------


def _write_self_signed_cert_pem(path: "pathlib.Path") -> None:
    """Write a minimal self-signed certificate + private key in PEM format
    to *path* (combined file suitable for ``load_cert_chain(certfile=…)``).

    Uses ``cryptography`` to produce a valid certificate + key so that
    ``build_ssl_context`` and ``_build_broker_ssl_context`` can load it.
    """
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-cert")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(cert_pem + b"\n" + key_pem)


def _write_self_signed_cert_pem_with_key(
    ca_path: "pathlib.Path",
    cert_path: "pathlib.Path",
    key_path: "pathlib.Path",
) -> None:
    """Write a self-signed certificate chain: CA PEM at *ca_path*,
    client certificate PEM at *cert_path*, and client private key
    PEM at *key_path*."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-ca")])
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_subject)
        .issuer_name(ca_subject)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

    client_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-client")])
    client_cert = (
        x509.CertificateBuilder()
        .subject_name(client_subject)
        .issuer_name(ca_subject)
        .public_key(client_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .sign(ca_key, hashes.SHA256())
    )
    cert_path.write_bytes(client_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        client_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


# ---------------------------------------------------------------------------
# HTTP integration tests — POST /move with triage_action=TO_CALENDAR
# ---------------------------------------------------------------------------


def _wait_for_mock_call(mock_obj: mock.MagicMock, timeout: float = 5.0) -> None:
    """Poll until *mock_obj* has been called at least once."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if mock_obj.call_count >= 1:
            return
        time.sleep(0.02)
    raise AssertionError(f"{mock_obj!r} was not called within {timeout}s")


def _wait_for_dispatch(mock_dispatch: mock.MagicMock, timeout: float = 5.0) -> None:
    """Poll until *mock_dispatch* has been called at least once."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if mock_dispatch.call_count >= 1:
            return
        time.sleep(0.02)
    raise AssertionError("dispatch_calendar_request was not called within timeout")


def _wait_for_triage_action(
    db_path: str, message_id: str, expected: str, timeout: float = 5.0
) -> None:
    """Poll until the triage action for *message_id* equals *expected*."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _triage_action(db_path, message_id) == expected:
            return
        time.sleep(0.02)
    actual = _triage_action(db_path, message_id)
    raise AssertionError(
        f"Expected triage action {expected!r}, got {actual!r} after {timeout}s"
    )


def _setup_db_with_record(
    db_path: str,
    message_id: str = "<cal-test@example.com>",
    *,
    body_plain: str = "Meeting on 2025-06-15 at 3:00 PM",
) -> None:
    """Insert a single mail record into *db_path*."""
    _populate_db(
        db_path,
        [
            {
                "message_id": message_id,
                "sender": "alice@example.com",
                "subject": "Calendar integration test",
                "date": "2025-06-10T10:00:00",
                "body_plain": body_plain,
                "status": "to_read",
            },
        ],
    )


_MOCK_DISPATCH_PATH = "robotsix_auto_mail.calendar.dispatch_calendar_request"


def test_move_to_calendar_dispatches_and_reroutes_to_archive(single_db: str) -> None:
    """Moving a card to TO_CALENDAR triggers dispatch and reroutes to
    TO_ARCHIVE when there is no prior TO_ANSWER triage decision."""
    _setup_db_with_record(single_db)

    with mock.patch(_MOCK_DISPATCH_PATH) as mock_dispatch:
        mock_dispatch.return_value = CalendarEventResponse(
            correlation_id="mock-cid",
            status="success",
            event_ref="Created event 'Test'",
        )
        server, port = _start_test_server(single_db)
        try:
            status, body = _post_form(
                port,
                {
                    "message_id": "<cal-test@example.com>",
                    "triage_action": "TO_CALENDAR",
                },
                path="/move",
            )
            # Success = 302 redirect (not a JSON response).
            assert status == 302, f"Expected 302, got {status}: {body}"

            # Dispatch runs in a background thread — wait for it.
            _wait_for_dispatch(mock_dispatch)

            # Verify dispatch was called with correct event.
            mock_dispatch.assert_called_once()
            event = mock_dispatch.call_args[0][0]
            assert isinstance(event, CalendarEventRequest)
            assert event.message_id == "<cal-test@example.com>"
            assert event.subject == "Calendar integration test"
            assert event.sender == "alice@example.com"
            assert "2025-06-15" in event.extracted_dates

            # Card should be rerouted to TO_ARCHIVE (no prior TO_ANSWER).
            _wait_for_triage_action(single_db, "<cal-test@example.com>", "TO_ARCHIVE")
        finally:
            server.shutdown()


def test_move_to_calendar_reroutes_to_answer_when_prior_was_to_answer(
    single_db: str,
) -> None:
    """When the prior triage action was TO_ANSWER, successful dispatch
    reroutes back to TO_ANSWER."""
    from robotsix_auto_mail.db import init_db
    from robotsix_auto_mail.triage import set_triage_decision

    _setup_db_with_record(single_db)

    # Seed a prior TO_ANSWER triage decision.
    conn = init_db(single_db)
    try:
        set_triage_decision(
            conn,
            "<cal-test@example.com>",
            "TO_ANSWER",
            source="agent",
            reason="needs reply",
        )
    finally:
        conn.close()

    with mock.patch(_MOCK_DISPATCH_PATH) as mock_dispatch:
        mock_dispatch.return_value = CalendarEventResponse(
            correlation_id="mock-cid",
            status="success",
            event_ref="Created event 'Test'",
        )
        server, port = _start_test_server(single_db)
        try:
            status, _ = _post_form(
                port,
                {
                    "message_id": "<cal-test@example.com>",
                    "triage_action": "TO_CALENDAR",
                },
                path="/move",
            )
            assert status == 302

            # Dispatch runs in a background thread — wait for it
            # and the reroute to TO_ANSWER.
            _wait_for_dispatch(mock_dispatch)
            _wait_for_triage_action(single_db, "<cal-test@example.com>", "TO_ANSWER")
        finally:
            server.shutdown()


def test_move_to_calendar_missing_message_id_returns_400(single_db: str) -> None:
    """POST /move without message_id returns 400."""
    server, port = _start_test_server(single_db)
    try:
        status, body = _post_form(
            port,
            {"triage_action": "TO_CALENDAR"},
            path="/move",
        )
        assert status == 400, f"Expected 400, got {status}: {body}"
        assert "Missing message_id" in body
    finally:
        server.shutdown()


def test_move_to_calendar_unknown_message_id_returns_404(single_db: str) -> None:
    """POST /move with unknown message_id returns 404."""
    server, port = _start_test_server(single_db)
    try:
        status, body = _post_form(
            port,
            {
                "message_id": "<nonexistent@example.com>",
                "triage_action": "TO_CALENDAR",
            },
            path="/move",
        )
        assert status == 404, f"Expected 404, got {status}: {body}"
        assert "Not found" in body
    finally:
        server.shutdown()


def test_move_to_calendar_dispatch_error_card_stays(single_db: str) -> None:
    """On CalendarDispatchError, the card stays in TO_CALENDAR (no reroute)
    and an error indicator is recorded on the card."""
    _setup_db_with_record(single_db)

    error_msg = "Calendar agent is not available"
    with (
        mock.patch(
            _MOCK_DISPATCH_PATH,
            side_effect=CalendarDispatchError(error_msg),
        ),
        mock.patch(
            "robotsix_auto_mail.db.update_calendar_event_ref"
        ) as mock_update_ref,
    ):
        server, port = _start_test_server(single_db)
        try:
            status, body = _post_form(
                port,
                {
                    "message_id": "<cal-test@example.com>",
                    "triage_action": "TO_CALENDAR",
                },
                path="/move",
            )
            # The handler still returns a 302 redirect (move succeeded,
            # calendar dispatch failed — error is on the card indicator).
            assert status == 302, f"Expected 302, got {status}: {body}"

            # Card must remain in TO_CALENDAR.
            assert _triage_action(single_db, "<cal-test@example.com>") == "TO_CALENDAR"

            # Error indicator must be recorded (runs in bg thread).
            _wait_for_mock_call(mock_update_ref)
            mock_update_ref.assert_called_once()
            args, _kwargs = mock_update_ref.call_args
            assert args[2] == f"error: {error_msg}", (
                f"Expected 'error: {error_msg}', got {args[2]!r}"
            )
        finally:
            server.shutdown()


def test_move_to_calendar_unexpected_error_card_stays(single_db: str) -> None:
    """On an unexpected exception during dispatch, the card stays in
    TO_CALENDAR (no reroute) and a generic error indicator is recorded."""
    _setup_db_with_record(single_db)

    with (
        mock.patch(
            _MOCK_DISPATCH_PATH,
            side_effect=RuntimeError("unexpected boom"),
        ),
        mock.patch(
            "robotsix_auto_mail.db.update_calendar_event_ref"
        ) as mock_update_ref,
    ):
        server, port = _start_test_server(single_db)
        try:
            status, body = _post_form(
                port,
                {
                    "message_id": "<cal-test@example.com>",
                    "triage_action": "TO_CALENDAR",
                },
                path="/move",
            )
            assert status == 302, f"Expected 302, got {status}: {body}"

            # Card must remain in TO_CALENDAR.
            assert _triage_action(single_db, "<cal-test@example.com>") == "TO_CALENDAR"

            # Error indicator must be recorded (runs in bg thread).
            _wait_for_mock_call(mock_update_ref)
            mock_update_ref.assert_called_once()
            args, _kwargs = mock_update_ref.call_args
            assert args[2] == "error: Internal error", (
                f"Expected 'error: Internal error', got {args[2]!r}"
            )
        finally:
            server.shutdown()


def test_move_to_calendar_realistic_message_id(single_db: str) -> None:
    """Moving a card with a Message-ID containing ``<``, ``>``, ``@``,
    ``+``, ``/``, ``=`` resolves the record (no 404) and dispatches.
    """
    message_id = "<abc+def/ghi=123@mail.example.com>"
    _setup_db_with_record(single_db, message_id=message_id)

    with mock.patch(_MOCK_DISPATCH_PATH) as mock_dispatch:
        mock_dispatch.return_value = CalendarEventResponse(
            correlation_id="mock-cid",
            status="success",
            event_ref="Created event 'Test'",
        )
        server, port = _start_test_server(single_db)
        try:
            status, body = _post_form(
                port,
                {"message_id": message_id, "triage_action": "TO_CALENDAR"},
                path="/move",
            )
            assert status == 302, f"Expected 302, got {status}: {body}"

            # Dispatch runs in a background thread — wait for it.
            _wait_for_dispatch(mock_dispatch)
            mock_dispatch.assert_called_once()
            # Card should be rerouted to TO_ARCHIVE.
            _wait_for_triage_action(single_db, message_id, "TO_ARCHIVE")
        finally:
            server.shutdown()


def test_move_to_calendar_angle_bracket_fallback(single_db: str) -> None:
    """Moving a card resolves the record even when the request omits angle
    brackets that the stored message_id includes (or vice versa)."""
    message_id_stored = "<cal-test@example.com>"
    message_id_posted = "cal-test@example.com"
    _setup_db_with_record(single_db, message_id=message_id_stored)

    with mock.patch(_MOCK_DISPATCH_PATH) as mock_dispatch:
        mock_dispatch.return_value = CalendarEventResponse(
            correlation_id="mock-cid",
            status="success",
            event_ref="Created event 'Test'",
        )
        server, port = _start_test_server(single_db)
        try:
            status, body = _post_form(
                port,
                {
                    "message_id": message_id_posted,
                    "triage_action": "TO_CALENDAR",
                },
                path="/move",
            )
            assert status == 302, f"Expected 302, got {status}: {body}"

            # Dispatch runs in a background thread — wait for it.
            _wait_for_dispatch(mock_dispatch)
            mock_dispatch.assert_called_once()
            # Card should be rerouted to TO_ARCHIVE.
            _wait_for_triage_action(single_db, message_id_stored, "TO_ARCHIVE")
        finally:
            server.shutdown()


def test_move_to_calendar_setup_failure_still_redirects(single_db: str) -> None:
    """When setup code (e.g. _effective_body_plain) raises, the move still
    returns 302 and the card lands in TO_CALENDAR with an error indicator."""
    _setup_db_with_record(single_db)

    with (
        mock.patch(
            "robotsix_auto_mail.format._effective_body_plain",
            side_effect=ValueError("body extraction failed"),
        ),
        mock.patch(
            "robotsix_auto_mail.db.update_calendar_event_ref"
        ) as mock_update_ref,
    ):
        server, port = _start_test_server(single_db)
        try:
            status, body = _post_form(
                port,
                {
                    "message_id": "<cal-test@example.com>",
                    "triage_action": "TO_CALENDAR",
                },
                path="/move",
            )
            # Must still redirect — no 500/502.
            assert status == 302, f"Expected 302, got {status}: {body}"

            # Card must land in TO_CALENDAR.
            assert _triage_action(single_db, "<cal-test@example.com>") == "TO_CALENDAR"

            # Error indicator must be recorded (synchronous — outer
            # except block runs in-request, no polling needed).
            mock_update_ref.assert_called_once()
            args, _kwargs = mock_update_ref.call_args
            assert args[2] == "error: Internal error", (
                f"Expected 'error: Internal error', got {args[2]!r}"
            )
        finally:
            server.shutdown()


def test_move_to_calendar_dispatch_hang_does_not_block(single_db: str) -> None:
    """When dispatch_calendar_request hangs forever, the /move request
    still returns 302 immediately (fire-and-forget background thread)."""
    import time

    _setup_db_with_record(single_db)

    # Make dispatch block forever.
    def _hang_forever(_event: object) -> None:
        while True:
            time.sleep(60)

    with mock.patch(_MOCK_DISPATCH_PATH, side_effect=_hang_forever):
        server, port = _start_test_server(single_db)
        try:
            t0 = time.monotonic()
            status, body = _post_form(
                port,
                {
                    "message_id": "<cal-test@example.com>",
                    "triage_action": "TO_CALENDAR",
                },
                path="/move",
            )
            elapsed = time.monotonic() - t0

            # Must return 302 quickly (well under 5 seconds).
            assert status == 302, f"Expected 302, got {status}: {body}"
            assert elapsed < 5.0, (
                f"Request took {elapsed:.1f}s — should return immediately"
            )

            # Card lands in TO_CALENDAR (synchronous set_triage_decision).
            assert _triage_action(single_db, "<cal-test@example.com>") == "TO_CALENDAR"
        finally:
            server.shutdown()


# ============================================================================
# Unit tests — extract_dates_from_body
# ============================================================================


def test_extract_dates_iso() -> None:
    result = extract_dates_from_body("2025-06-15")
    assert result == ["2025-06-15"]


def test_extract_dates_us_slash() -> None:
    result = extract_dates_from_body("6/15/2025")
    assert result == ["6/15/2025"]


def test_extract_dates_dotted() -> None:
    result = extract_dates_from_body("15.06.2025")
    assert result == ["15.06.2025"]


def test_extract_dates_month_name() -> None:
    result = extract_dates_from_body("Jun 15")
    assert result == ["Jun 15"]


def test_extract_dates_month_full() -> None:
    result = extract_dates_from_body("December 25")
    assert result == ["December 25"]


def test_extract_dates_time_12h() -> None:
    result = extract_dates_from_body("3:00 PM")
    assert result == ["3:00 PM"]


def test_extract_dates_time_24h() -> None:
    result = extract_dates_from_body("14:30")
    assert result == ["14:30"]


def test_extract_dates_multiple() -> None:
    result = extract_dates_from_body("2025-06-15 at 3:00 PM and 6/15/2025")
    assert result == ["2025-06-15", "3:00 PM", "6/15/2025"]


def test_extract_dates_empty_string() -> None:
    result = extract_dates_from_body("")
    assert result == []


def test_extract_dates_no_match() -> None:
    result = extract_dates_from_body("No dates here")
    assert result == []


def test_extract_dates_caps_at_10() -> None:
    # 15 ISO dates, only 10 should be returned.
    body = " ".join("2025-06-{:02d}".format(i) for i in range(1, 16))
    result = extract_dates_from_body(body)
    assert len(result) == 10


def test_extract_dates_deduplicates() -> None:
    result = extract_dates_from_body("2025-06-15 2025-06-15")
    assert result == ["2025-06-15"]


# ============================================================================
# Unit tests — extract_calendar_summary
# ============================================================================


def test_summary_includes_subject() -> None:
    from tests.conftest import _make_record

    record = _make_record(subject="Lunch meeting")
    result = extract_calendar_summary(record)
    assert "Subject: Lunch meeting" in result


def test_summary_includes_formatted_date() -> None:
    from tests.conftest import _make_record

    record = _make_record(date="2025-06-15T12:00:00")
    result = extract_calendar_summary(record)
    assert "Email date:" in result
    assert "2025-06-15" in result


def test_summary_includes_extracted_dates() -> None:
    from tests.conftest import _make_record

    record = _make_record(body_plain="Meet on 2025-06-20")
    result = extract_calendar_summary(record)
    assert "Date/time references in body: 2025-06-20" in result


def test_summary_empty_subject_shows_placeholder() -> None:
    from tests.conftest import _make_record

    record = _make_record(subject="")
    result = extract_calendar_summary(record)
    assert "Subject: (no subject)" in result


def test_summary_whitespace_only_subject() -> None:
    from tests.conftest import _make_record

    record = _make_record(subject="   ")
    result = extract_calendar_summary(record)
    assert "Subject: (no subject)" in result


def test_summary_no_body_omits_date_references() -> None:
    from tests.conftest import _make_record

    record = _make_record(body_plain="", body_html="")
    result = extract_calendar_summary(record)
    assert "Date/time references" not in result


def test_summary_no_dates_in_body_omits_date_references() -> None:
    from tests.conftest import _make_record

    record = _make_record(body_plain="Hello world")
    result = extract_calendar_summary(record)
    assert "Date/time references" not in result
