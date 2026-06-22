"""Transport factory for calendar agent-comm dispatch.

Exposes ``build_calendar_transport`` — a single helper that returns the
objects an ``Agent`` is constructed with, selecting between the existing
in-process ``Registry`` path and the brokered client transport added by
the broker epic (epic child 1 / agent-comm PR #92).

All ``robotsix_agent_comm`` imports are lazy and guarded so the server
remains functional when the optional dependency is not installed.
Brokered symbols are accessed dynamically via ``getattr`` to keep mypy
green against the pinned dep (which predates the broker work).
"""

from __future__ import annotations

import logging
import ssl
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from robotsix_auto_mail.config.model import MailConfig

logger = logging.getLogger(__name__)

# Module-level singleton ``Registry`` for in-process mode, mirroring the
# historical ``_get_registry()`` behaviour.
_in_process_registry: object | None = None


def _get_in_process_registry() -> object:
    """Return the module-level ``Registry`` singleton, creating it on demand."""
    global _in_process_registry
    if _in_process_registry is None:
        from robotsix_agent_comm.transport import Registry

        _in_process_registry = Registry()
    return _in_process_registry


def build_ssl_context(
    ca_path: str,
    client_cert_path: str = "",
    client_key_path: str = "",
) -> ssl.SSLContext:
    """Build a TLS client-side ``ssl.SSLContext`` from PEM files.

    Uses ``PROTOCOL_TLS_CLIENT`` (modern default: cert validation + hostname
    checking enabled).  When *client_cert_path* / *client_key_path* are
    non-empty, loads the client certificate chain for mutual TLS.

    This is a pure function (no I/O beyond reading the cert files), so it
    is unit-testable with real certs generated on the fly.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cafile=ca_path)
    if client_cert_path:
        if client_key_path:
            ctx.load_cert_chain(certfile=client_cert_path, keyfile=client_key_path)
        else:
            # Combined PEM file (cert + key in one file).
            ctx.load_cert_chain(certfile=client_cert_path)
    return ctx


def build_calendar_transport(
    *,
    mode: str,
    broker_host: str = "",
    broker_port: int = 8443,
    ca_path: str = "",
    client_cert_path: str = "",
    client_key_path: str = "",
    token: str = "",
) -> tuple[object, object | None]:
    """Return ``(registry, transport)`` for ``Agent`` construction.

    Parameters:
        mode: ``"in-process"`` (default) or ``"brokered"``.
        broker_host: Broker server hostname (required for brokered).
        broker_port: Broker server port (default 443).
        ca_path: Path to a custom CA certificate PEM. Optional — when empty,
            the system trust store is used (the deployed broker is fronted by
            a publicly-trusted TLS endpoint).
        client_cert_path: Path to client certificate PEM (mutual TLS).
        client_key_path: Path to client key PEM (mutual TLS).
        token: Agent authentication token (required for brokered).

    Returns:
        ``(registry, transport)`` — the registry is always set; transport
        is ``None`` for in-process mode.

    Raises:
        ValueError: When *mode* is ``"brokered"`` but required fields
            (host, token) are missing.
        ImportError: When ``robotsix_agent_comm`` is not installed and
            a brokered transport is requested (callers must catch).
    """
    if mode == "in-process":
        return (_get_in_process_registry(), None)

    if mode != "brokered":
        raise ValueError(
            f"Unknown calendar transport mode {mode!r}; "
            f"expected 'in-process' or 'brokered'"
        )

    # Validate required brokered fields. A CA file is optional — the deployed
    # broker uses a publicly-trusted cert, so system trust is the default.
    missing: list[str] = []
    if not broker_host:
        missing.append("host")
    if not token:
        missing.append("token")
    if missing:
        raise ValueError(
            "Calendar broker configuration incomplete: missing " + ", ".join(missing)
        )

    # Dynamic attribute access on transport module — keeps mypy green even when
    # the pinned robotsix_agent_comm predates the broker symbols.
    import importlib

    transport_mod = importlib.import_module("robotsix_agent_comm.transport")

    BrokeredRegistry = getattr(transport_mod, "BrokeredRegistry")
    NetworkedBrokerTransport = getattr(transport_mod, "NetworkedBrokerTransport")

    # Custom CA only when provided; otherwise default system trust.
    if ca_path:
        ssl_context = build_ssl_context(ca_path, client_cert_path, client_key_path)
    else:
        ssl_context = ssl.create_default_context()
        if client_cert_path:
            if client_key_path:
                ssl_context.load_cert_chain(
                    certfile=client_cert_path, keyfile=client_key_path
                )
            else:
                ssl_context.load_cert_chain(certfile=client_cert_path)

    registry = BrokeredRegistry(
        broker_host,
        broker_port,
        scheme="https",
        ssl_context=ssl_context,
        agent_token=token,
    )
    transport = NetworkedBrokerTransport(
        broker_host,
        broker_port,
        scheme="https",
        ssl_context=ssl_context,
        agent_token=token,
    )
    return (registry, transport)


def build_calendar_transport_from_config(
    config: MailConfig,
) -> tuple[object, object | None]:
    """Convenience wrapper that reads transport settings from *config*.

    Raises the same exceptions as :func:`build_calendar_transport`.
    """
    return build_calendar_transport(
        mode=config.calendar_transport,
        broker_host=config.calendar_broker_host,
        broker_port=config.calendar_broker_port,
        ca_path=config.calendar_broker_tls_ca,
        client_cert_path=config.calendar_broker_client_cert,
        client_key_path=config.calendar_broker_client_key,
        token=config.calendar_broker_token,
    )
