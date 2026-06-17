"""Tests for the calendar transport factory (transport.py)."""

from __future__ import annotations

import ssl
import sys
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.calendar.transport import (
    build_calendar_transport,
    build_calendar_transport_from_config,
    build_ssl_context,
)
from robotsix_auto_mail.config import MailConfig

# ---------------------------------------------------------------------------
# Fixtures — fake agent-comm modules
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_agent_comm_modules():
    """Ensure no stale fake modules leak between tests."""
    for key in list(sys.modules):
        if key.startswith("robotsix_agent_comm"):
            del sys.modules[key]
    # Also clear the module-level singleton cache.
    import robotsix_auto_mail.calendar.transport as tmod

    tmod._in_process_registry = None
    yield
    for key in list(sys.modules):
        if key.startswith("robotsix_agent_comm"):
            del sys.modules[key]
    tmod._in_process_registry = None


def _install_minimal_agent_comm(*, include_brokered: bool = True):
    """Install synthetic ``robotsix_agent_comm.*`` modules.

    When *include_brokered* is True, the transport module also carries
    ``BrokeredRegistry`` and ``NetworkedBrokerTransport``.
    """
    transport_mod = mock.MagicMock()
    transport_mod.Registry = mock.MagicMock

    if include_brokered:
        transport_mod.BrokeredRegistry = mock.MagicMock
        transport_mod.NetworkedBrokerTransport = mock.MagicMock

    sdk_mod = mock.MagicMock()
    sdk_mod.Agent = mock.MagicMock

    sys.modules["robotsix_agent_comm"] = mock.MagicMock()
    sys.modules["robotsix_agent_comm.sdk"] = sdk_mod
    sys.modules["robotsix_agent_comm.transport"] = transport_mod

    return transport_mod


# ---------------------------------------------------------------------------
# build_ssl_context
# ---------------------------------------------------------------------------


def _generate_ca_cert(tmp_path: Path) -> tuple[Path, Path]:
    """Generate a self-signed CA cert + key in *tmp_path*.

    Returns ``(ca_path, key_path)``.
    """
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-ca")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )

    key_path = tmp_path / "ca-key.pem"
    ca_path = tmp_path / "ca.pem"

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    ca_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    return ca_path, key_path


def _generate_client_cert(
    ca_key_path: Path,
    ca_cert_path: Path,
    tmp_path: Path,
    *,
    with_key: bool = True,
) -> tuple[Path, Path]:
    """Generate a client cert signed by the CA.

    Returns ``(cert_path, key_path)``.  When *with_key* is False, returns
    a combined PEM file path as the first element and the key path is
    the same file.
    """
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    # Load CA key.
    ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)

    client_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-client")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(x509.load_pem_x509_certificate(ca_cert_path.read_bytes()).subject)
        .public_key(client_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    client_key_bytes = client_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_bytes = cert.public_bytes(serialization.Encoding.PEM)

    if with_key:
        cert_path = tmp_path / "client.pem"
        key_path = tmp_path / "client-key.pem"
        cert_path.write_bytes(cert_bytes)
        key_path.write_bytes(client_key_bytes)
        return cert_path, key_path
    else:
        combined = tmp_path / "client-combined.pem"
        combined.write_bytes(cert_bytes + b"\n" + client_key_bytes)
        return combined, combined


def test_build_ssl_context_ca_only(tmp_path) -> None:
    """build_ssl_context with only CA cert works."""
    ca_path, _ = _generate_ca_cert(tmp_path)
    ctx = build_ssl_context(str(ca_path))
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.protocol == ssl.PROTOCOL_TLS_CLIENT


def test_build_ssl_context_with_client_cert(tmp_path) -> None:
    """build_ssl_context with CA + client cert + key works."""
    ca_path, ca_key_path = _generate_ca_cert(tmp_path)
    cert_path, key_path = _generate_client_cert(ca_key_path, ca_path, tmp_path)
    ctx = build_ssl_context(str(ca_path), str(cert_path), str(key_path))
    assert isinstance(ctx, ssl.SSLContext)


def test_build_ssl_context_combined_pem(tmp_path) -> None:
    """build_ssl_context with combined client cert+key PEM works."""
    ca_path, ca_key_path = _generate_ca_cert(tmp_path)
    combined, _ = _generate_client_cert(ca_key_path, ca_path, tmp_path, with_key=False)
    ctx = build_ssl_context(str(ca_path), str(combined))
    assert isinstance(ctx, ssl.SSLContext)


def test_build_ssl_context_missing_ca_file() -> None:
    """build_ssl_context raises FileNotFoundError for missing CA."""
    with pytest.raises(FileNotFoundError):
        build_ssl_context("/nonexistent/ca.pem")


# ---------------------------------------------------------------------------
# build_calendar_transport — in-process mode
# ---------------------------------------------------------------------------


def test_build_in_process_returns_registry_and_none_transport() -> None:
    """in-process mode returns (registry, None)."""
    _install_minimal_agent_comm()

    registry, transport = build_calendar_transport(mode="in-process")
    assert registry is not None
    assert transport is None


def test_build_in_process_reuses_singleton() -> None:
    """in-process Registry is a module-level singleton."""
    _install_minimal_agent_comm()

    r1, _ = build_calendar_transport(mode="in-process")
    r2, _ = build_calendar_transport(mode="in-process")
    assert r1 is r2


def test_build_in_process_missing_dep_raises_import_error() -> None:
    """When robotsix_agent_comm is not installed, import fails."""
    # No modules installed — should raise ImportError from within
    # the lazy import inside _get_in_process_registry.
    with pytest.raises(ImportError):
        build_calendar_transport(mode="in-process")


# ---------------------------------------------------------------------------
# build_calendar_transport — brokered mode
# ---------------------------------------------------------------------------


def test_build_brokered_returns_registry_and_transport() -> None:
    """brokered mode with valid config returns (registry, transport)."""
    _install_minimal_agent_comm()

    # We need a real CA file for build_ssl_context.  Create a temporary one.
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
        # Minimal self-signed cert for testing (just needs to be parseable).
        import datetime

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=None),
                critical=True,
            )
            .sign(key, hashes.SHA256())
        )
        f.write(cert.public_bytes(serialization.Encoding.PEM))
        ca_path = f.name

    try:
        registry, transport = build_calendar_transport(
            mode="brokered",
            broker_host="localhost",
            broker_port=8443,
            ca_path=ca_path,
            token="test-token",
        )
        assert registry is not None
        assert transport is not None
    finally:
        Path(ca_path).unlink(missing_ok=True)


def test_build_brokered_missing_host_raises_value_error() -> None:
    """Brokered mode without host raises ValueError."""
    _install_minimal_agent_comm()

    with pytest.raises(ValueError, match="missing host"):
        build_calendar_transport(
            mode="brokered",
            broker_host="",
            ca_path="/some/ca.pem",
            token="test-token",
        )


def test_build_brokered_missing_ca_raises_value_error() -> None:
    """Brokered mode without CA raises ValueError."""
    _install_minimal_agent_comm()

    with pytest.raises(ValueError, match=r"missing.*TLS CA"):
        build_calendar_transport(
            mode="brokered",
            broker_host="localhost",
            ca_path="",
            token="test-token",
        )


def test_build_brokered_missing_token_raises_value_error() -> None:
    """Brokered mode without token raises ValueError."""
    _install_minimal_agent_comm()

    with pytest.raises(ValueError, match=r"missing.*token"):
        build_calendar_transport(
            mode="brokered",
            broker_host="localhost",
            ca_path="/some/ca.pem",
            token="",
        )


def test_build_brokered_missing_multiple_fields() -> None:
    """Brokered mode missing multiple fields lists all in error."""
    _install_minimal_agent_comm()

    with pytest.raises(ValueError, match="missing host, TLS CA, token"):
        build_calendar_transport(
            mode="brokered",
            broker_host="",
            ca_path="",
            token="",
        )


def test_build_brokered_import_error_raises() -> None:
    """When robotsix_agent_comm is not installed, brokered raises ImportError."""
    # No fake modules — the import inside build_calendar_transport
    # should raise ImportError because importlib.import_module fails.
    with pytest.raises(ImportError):
        build_calendar_transport(
            mode="brokered",
            broker_host="localhost",
            ca_path="/nonexistent/ca.pem",
            token="test-token",
        )


def test_build_unknown_mode_raises_value_error() -> None:
    """Unknown mode raises ValueError."""
    _install_minimal_agent_comm()

    with pytest.raises(ValueError, match="Unknown calendar transport mode"):
        build_calendar_transport(mode="unknown")


# ---------------------------------------------------------------------------
# build_calendar_transport_from_config
# ---------------------------------------------------------------------------


def test_build_from_config_in_process() -> None:
    """build_calendar_transport_from_config with default config returns in-process."""
    _install_minimal_agent_comm()

    cfg = MailConfig(imap_host="h", smtp_host="h", username="u", password="p")
    registry, transport = build_calendar_transport_from_config(cfg)
    assert registry is not None
    assert transport is None


def test_build_from_config_brokered() -> None:
    """build_calendar_transport_from_config with brokered config works."""
    import tempfile
    from pathlib import Path

    _install_minimal_agent_comm()

    # Generate a throwaway CA cert.
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )

    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
        ca_path = f.name

    try:
        cfg = MailConfig(
            imap_host="h",
            smtp_host="h",
            username="u",
            password="p",
            calendar_transport="brokered",
            calendar_broker_host="localhost",
            calendar_broker_port=8443,
            calendar_broker_tls_ca=ca_path,
            calendar_broker_token="test-token",
        )
        registry, transport = build_calendar_transport_from_config(cfg)
        assert registry is not None
        assert transport is not None
    finally:
        Path(ca_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Config field defaults
# ---------------------------------------------------------------------------


def test_calendar_transport_defaults_to_in_process() -> None:
    """Default MailConfig has calendar_transport='in-process'."""
    cfg = MailConfig(imap_host="h", smtp_host="h", username="u", password="p")
    assert cfg.calendar_transport == "in-process"


def test_calendar_broker_port_defaults_to_8443() -> None:
    """Default broker port is 8443."""
    cfg = MailConfig(imap_host="h", smtp_host="h", username="u", password="p")
    assert cfg.calendar_broker_port == 8443


def test_calendar_broker_token_is_masked_in_repr() -> None:
    """calendar_broker_token is in _SECRET_FIELDS and masked."""
    cfg = MailConfig(
        imap_host="h",
        smtp_host="h",
        username="u",
        password="p",
        calendar_broker_token="s3cret",
    )
    r = repr(cfg)
    assert "s3cret" not in r
    assert "calendar_broker_token=<redacted>" in r
