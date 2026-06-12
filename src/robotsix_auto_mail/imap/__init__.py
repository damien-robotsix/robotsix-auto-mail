"""IMAP client built on stdlib ``imaplib``.

Provides ``ImapClient`` - a context manager that connects to a real IMAP
server, negotiates TLS, authenticates, and exposes ``list_folders()``,
``select_folder()``, and ``create_folder()`` for mailbox inspection and
creation.

Depends only on ``MailConfig`` from ``robotsix_auto_mail.config`` and the
Python standard library (``imaplib``, ``ssl``).
"""

from __future__ import annotations

import dataclasses
import imaplib
import re
import shlex
import ssl
from typing import Any

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.oauth2 import build_token_provider
from robotsix_auto_mail.protocol import _ProtocolClient, build_xoauth2_response

# Store a reference to IMAP4.error *before* any mocking can replace
# IMAP4 and turn ``IMAP4.error`` into a MagicMock attribute.  Using
# this reference in except clauses keeps tests reliable.
_IMAP4_ERROR = imaplib.IMAP4.error

# Maximum number of UIDs packed into a single ``UID STORE`` / ``UID COPY``
# round-trip by the batched delete/move primitives.  A 518-mail column then
# costs at most six round-trip pairs instead of one per message.
_BATCH_UID_CHUNK = 100

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ImapError(Exception):
    """Base exception for all IMAP client errors."""


class ImapConnectionError(ImapError):
    """Socket-level or IMAP greeting failure.

    Wraps ``OSError`` / ``socket.gaierror`` (unreachable host, connection
    refused, timeout) and ``imaplib.IMAP4.error`` from a bad server greeting.
    """


class ImapTlsError(ImapError):
    """TLS negotiation failure.

    Wraps ``STARTTLS`` capability-not-advertised, TLS handshake errors
    (``ssl.SSLError``), and protocol errors during the STARTTLS exchange.
    """


class ImapAuthError(ImapError):
    """Authentication failure.

    Wraps ``imaplib.IMAP4.error`` raised by ``login()`` when the server
    responds with ``'NO'`` or ``'BAD'``.
    """


class ImapMessageNotFoundError(ImapError):
    """The target UID does not exist in the selected folder (stale UID)."""


# ---------------------------------------------------------------------------
# MailboxInfo
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class MailboxInfo:
    """Describes a single mailbox (folder) on the IMAP server.

    Attributes:
        name: Decoded mailbox name (e.g. ``"INBOX"``,
            ``"[Gmail]/Sent Mail"``).
        attributes: Tuple of flag strings from the LIST response
            (e.g. ``('\\\\HasNoChildren',)`` for a leaf folder,
            ``('\\\\HasChildren', '\\\\Noselect')`` for a namespace node).
        delimiter: Hierarchy delimiter character (e.g. ``"/"``, or ``""``
            when the server uses a flat namespace).
    """

    name: str
    attributes: tuple[str, ...]
    delimiter: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_list_line(line: bytes) -> MailboxInfo:
    """Parse a single IMAP ``LIST`` response line into a ``MailboxInfo``.

    Expected format (RFC 3501)::

        (FLAGS) DELIMITER NAME

    Examples::

        (\\HasNoChildren) "/" "INBOX"
        (\\HasChildren \\Noselect) "/" "[Gmail]"
        () "/" "Sent Mail"
        (\\HasNoChildren) NIL "INBOX"
    """
    text = line.decode("utf-8", errors="replace")

    # Flags are always in the first parenthesised group.
    if not text.startswith("("):
        raise ValueError(f"Invalid LIST response: {text!r}")
    end = text.find(")")
    if end < 0:
        raise ValueError(f"Invalid LIST response: {text!r}")

    flags_str = text[1:end]
    rest = text[end + 1 :].strip()

    # The remaining tokens (delimiter + name) may be quoted or NIL.
    try:
        tokens = shlex.split(rest)
    except ValueError as exc:
        raise ValueError(f"Invalid LIST response: {text!r}") from exc

    if len(tokens) < 2:
        raise ValueError(f"Invalid LIST response: {text!r}")

    # -- flags -----------------------------------------------------------
    if flags_str.strip():
        attributes: tuple[str, ...] = tuple(f.strip() for f in flags_str.split())
    else:
        attributes = ()

    # -- delimiter -------------------------------------------------------
    delimiter = "" if tokens[0].upper() == "NIL" else tokens[0]

    # -- name ------------------------------------------------------------
    name = tokens[1]

    return MailboxInfo(name=name, attributes=attributes, delimiter=delimiter)


def resolve_uid_with_fallback(
    client: "ImapClient",
    source_folder: str,
    uid: int,
    message_id: str,
) -> int:
    """Return a confirmed UID for *message_id* in *source_folder*.

    First tries the stored *uid*.  If that UID is not found in
    *source_folder*, falls back to a ``HEADER Message-ID`` search
    (UIDs can shift when servers renumber).  Raises
    ``ImapMessageNotFoundError`` if neither approach finds the
    message.

    The caller must have already selected a folder or be prepared
    for this function to select *source_folder*.
    """
    client.select_folder(source_folder)
    if client.search_uids(f"UID {uid}"):
        return uid
    # Fallback: UID may have shifted — search by Message-ID header.
    found = client.search_uids(f'HEADER Message-ID "{message_id}"')
    if found:
        return found[0]
    raise ImapMessageNotFoundError(
        f"UID {uid} not found in {source_folder!r} "
        f"(Message-ID fallback also failed)"
    )


# ---------------------------------------------------------------------------
# ImapClient
# ---------------------------------------------------------------------------


class ImapClient(_ProtocolClient):
    """Context-managed IMAP client.

    Constructor accepts a ``MailConfig`` and extracts only the IMAP-relevant
    fields (``imap_host``, ``imap_port``, ``imap_tls_mode``, ``username``,
    ``password``).  The SMTP fields are never referenced.

    Typical usage::

        cfg = MailConfig.from_env()
        with ImapClient(cfg) as client:
            folders = client.list_folders()
            count = client.select_folder("INBOX")
    """

    def __init__(self, config: MailConfig) -> None:
        super().__init__(
            host=config.imap_host,
            port=config.imap_port,
            tls_mode=config.imap_tls_mode,
            username=config.username,
            password=config.password,
            oauth2_token=config.oauth2_token,
            oauth2_client_id=config.oauth2_client_id,
            oauth2_client_secret=config.oauth2_client_secret,
        )
        self._token_provider = build_token_provider(config)

        self._imap: imaplib.IMAP4 | None = None

    # -- read-only server metadata ---------------------------------------

    @property
    def server_greeting(self) -> bytes | None:
        """Server greeting (``welcome`` line), or ``None`` when not connected."""
        if self._imap is None:
            return None
        return self._imap.welcome

    @property
    def capabilities(self) -> tuple[str, ...]:
        """Server capabilities, or ``()`` when not connected."""
        if self._imap is None:
            return ()
        return self._imap.capabilities

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> ImapClient:
        """Connect + authenticate, returning the ready-to-use client."""
        self._dispatch_tls()
        self._authenticate()
        return self

    def __exit__(self, *args: Any) -> None:
        """Log out and close the socket, even if an exception occurred."""
        if self._imap is not None:
            try:
                self._imap.logout()
            except Exception:  # noqa: S110  # nosec B110
                # Connection may already be dead - best-effort close.
                pass
        # In case logout() left the socket dangling, close it ourselves.
        self._close_socket()

    # -- connection helpers ------------------------------------------------

    def _connect_direct_tls(self) -> None:
        ctx = ssl.create_default_context()
        try:
            self._imap = imaplib.IMAP4_SSL(self._host, self._port, ssl_context=ctx)
        except (OSError, _IMAP4_ERROR) as exc:
            raise ImapConnectionError(
                f"Direct-TLS connection to {self._host}:{self._port} failed: {exc}"
            ) from exc

    def _connect_starttls(self) -> None:
        # 1. Plain connection
        try:
            self._imap = imaplib.IMAP4(self._host, self._port)
        except (OSError, _IMAP4_ERROR) as exc:
            raise ImapConnectionError(
                f"Plain connection to {self._host}:{self._port} failed: {exc}"
            ) from exc

        # 2. Upgrade to TLS
        ctx = ssl.create_default_context()
        try:
            self._imap.starttls(ssl_context=ctx)
        except (_IMAP4_ERROR, ssl.SSLError, OSError) as exc:
            # Close the plain connection before raising - it is now
            # in an unknown state.
            self._close_socket()
            raise ImapTlsError(
                f"STARTTLS negotiation with {self._host}:{self._port} failed: {exc}"
            ) from exc

    def _connect_plain(self) -> None:
        try:
            self._imap = imaplib.IMAP4(self._host, self._port)
        except (OSError, _IMAP4_ERROR) as exc:
            raise ImapConnectionError(
                f"Plain (no-TLS) connection to {self._host}:{self._port} failed: {exc}"
            ) from exc

    def _authenticate(self) -> None:
        if self._imap is None:
            raise RuntimeError("_authenticate() called before _connect_*()")
        if self._token_provider is not None:
            self._oauth2_token = self._token_provider()
        try:
            if self._token_provider is not None or self._oauth2_token:
                self._imap.authenticate("XOAUTH2", self._imap_xoauth2_cb)
            else:
                self._imap.login(self._username, self._password)
        except _IMAP4_ERROR as exc:
            raise ImapAuthError(
                f"Authentication failed for user {self._username!r} "
                f"on {self._host}:{self._port}: {exc}"
            ) from exc

    def _imap_xoauth2_cb(self, challenge: bytes) -> bytes | None:
        """SASL XOAUTH2 callback for ``imaplib.IMAP4.authenticate()``.

        On the initial (empty) challenge returns the XOAUTH2 response
        string.  On any non-empty challenge (server-side error) cancels
        with ``None``.
        """
        if challenge:
            return None
        resp = build_xoauth2_response(self._username, self._oauth2_token)
        return resp.encode()

    def _close_socket(self) -> None:
        """Best-effort socket close when ``logout()`` is not viable."""
        if self._imap is None:
            return
        try:
            sock = getattr(self._imap, "sock", None)
            if sock is not None:
                sock.close()
        except Exception:  # noqa: S110  # nosec B110
            pass

    # -- public methods ----------------------------------------------------

    def list_folders(self) -> list[MailboxInfo]:
        """Issue ``LIST "" "*"`` and return parsed mailbox metadata.

        Returns:
            A list of ``MailboxInfo`` objects describing every mailbox
            visible to the authenticated user.

        Raises:
            ImapError: If the client is not connected or the server
                returns a non-OK response.
        """
        if self._imap is None:
            raise ImapError("Not connected")
        status, data = self._imap.list()
        if status != "OK":
            raise ImapError(f"LIST command failed: {status}")
        result: list[MailboxInfo] = []
        for line in data:
            if isinstance(line, bytes):
                result.append(_parse_list_line(line))
        return result

    def select_folder(self, name: str) -> int:
        """Select a mailbox and return the message count.

        Args:
            name: Mailbox name (e.g. ``"INBOX"``).

        Returns:
            The number of messages in the mailbox, or ``0`` when the
            server does not provide an EXISTS count in the response.

        Raises:
            ImapError: If the client is not connected or the server
                returns a non-OK response.
        """
        if self._imap is None:
            raise ImapError("Not connected")
        status, data = self._imap.select(name)
        if status != "OK":
            raise ImapError(f"SELECT '{name}' failed: {status}")
        if data and data[0]:
            try:
                return int(data[0])
            except (ValueError, TypeError):
                return 0
        return 0

    def create_folder(self, name: str) -> None:
        """Create a mailbox (folder) on the server, idempotently.

        Issues an IMAP ``CREATE``.  When the server responds with a
        non-OK status the folder may already exist (servers commonly
        reply ``NO`` with text containing ``ALREADYEXISTS`` /
        ``already exists``).  In that case the existing folder is
        treated as success: the folder list is re-fetched and, if a
        mailbox with the same name is present, the method returns
        silently.

        Args:
            name: Mailbox name to create (e.g.
                ``"robotsix-mail-archive/2026"``).

        Raises:
            ImapError: If the client is not connected, or the server
                returns a non-OK status and no mailbox with ``name``
                exists in the folder list.
        """
        if self._imap is None:
            raise ImapError("Not connected")
        status, _data = self._imap.create(name)
        if status == "OK":
            self._subscribe(name)
            return
        # Inspect the response data for an ALREADYEXISTS signal (common on
        # Dovecot, Courier, etc.).  If the server tells us the folder
        # already exists we can return immediately without re-listing.
        response_text = b"".join(_data).decode("utf-8", errors="replace").strip()
        lowered = response_text.lower()
        if "alreadyexists" in lowered or "already exists" in lowered:
            self._subscribe(name)
            return
        # Non-OK without an ALREADYEXISTS signal: the folder may still
        # already exist despite a non-specific NO.  Re-list and check.
        for folder in self.list_folders():
            if folder.name == name:
                self._subscribe(name)
                return
        raise ImapError(f"CREATE '{name}' failed: {status} — {response_text}")

    def _subscribe(self, name: str) -> None:
        """Subscribe to *name*; ignore failure silently."""
        if self._imap is None:
            return
        try:
            self._imap.subscribe(name)
        except Exception:  # noqa: S110  # nosec B110
            pass

    def search_uids(self, criteria: str = "ALL") -> list[int]:
        """Issue ``UID SEARCH`` and return matching UIDs.

        Args:
            criteria: IMAP search criteria (default ``"ALL"``).
                For watermark-based incremental fetch use e.g.
                ``"UID 42:*"``.

        Returns:
            Sorted list of numeric UIDs.  Empty when no messages match.

        Raises:
            ImapError: If not connected or the server returns non-OK.
        """
        if self._imap is None:
            raise ImapError("Not connected")
        status, data = self._imap.uid("SEARCH", criteria)
        if status != "OK":
            raise ImapError(f"UID SEARCH failed: {status}")
        # data is a list containing one element: the space-separated UIDs.
        if not data or not data[0]:
            return []
        try:
            uid_str = data[0].decode("utf-8", errors="replace").strip()
        except (AttributeError, LookupError):
            return []
        if not uid_str:
            return []
        return [int(uid) for uid in uid_str.split()]

    def fetch_messages(self, uids: list[int]) -> list[tuple[int, bytes]]:
        """Fetch raw message bodies by UID without setting ``\\Seen``.

        Uses ``BODY.PEEK[]`` so the server does NOT mark messages as
        read.

        Args:
            uids: List of IMAP UIDs to fetch.

        Returns:
            List of ``(uid, raw_mime_bytes)`` pairs.  UIDs that no
            longer exist on the server are silently omitted.

        Raises:
            ImapError: If not connected or the server returns non-OK.
        """
        if self._imap is None:
            raise ImapError("Not connected")
        if not uids:
            return []

        uid_set = ",".join(str(uid) for uid in uids)
        status, data = self._imap.uid("FETCH", uid_set, "(BODY.PEEK[])")
        if status != "OK":
            raise ImapError(f"UID FETCH failed: {status}")

        result: list[tuple[int, bytes]] = []
        # imaplib returns untagged FETCH responses in data as a list of
        # alternating (header_bytes, literal_bytes) pairs.  We walk the
        # list and, for each pair, extract the UID from the header line
        # and pair it with the literal body.
        #
        # Two server shapes are handled:
        #  * Header-first (OVH, most servers): the UID is embedded in the
        #    tuple header, e.g. ``(b"1 (UID 42 BODY[] {5}", body)``.
        #  * Trailing-UID (Exchange / Office365): the header carries no
        #    UID and the UID arrives as a separate bare-bytes item
        #    immediately after the tuple, e.g.
        #    ``[(b"1 (BODY[] {5}", body), b" UID 42)"]``.
        pending_body: bytes | None = None
        for item in data:
            if isinstance(item, tuple) and len(item) == 2:
                header, body = item
                if not isinstance(header, bytes) or not isinstance(body, bytes):
                    pending_body = None
                    continue
                # Parse UID from the header line, e.g.:
                # b'1 (UID 42)'
                # b'1 (UID 42 BODY[] {5}'
                uid = self._parse_uid_from_fetch_header(header)
                if uid is not None:
                    result.append((uid, body))
                    pending_body = None
                else:
                    # No UID in the header — Exchange/Office365 places it
                    # in the bare-bytes item that follows.
                    pending_body = body
            elif isinstance(item, bytes) and pending_body is not None:
                # Trailing bare-bytes UID carrier for the preceding
                # header-less tuple, e.g. b" UID 10780)".
                uid = self._parse_uid_from_fetch_trailer(item)
                if uid is not None:
                    result.append((uid, pending_body))
                pending_body = None
            else:
                # Stray bare-bytes continuation (e.g. b")") with no
                # pending header-less body — ignore, as before.
                pending_body = None

        return result

    def delete_message(self, uid: int) -> None:
        """Mark *uid* as ``\\Deleted`` and expunge the selected mailbox.

        Raises :class:`ImapError` if not connected or the server
        returns a non-OK status for either the ``UID STORE`` or the
        ``EXPUNGE`` operation.
        """
        if self._imap is None:
            raise ImapError("Not connected")

        # Pre-verify the UID exists in the selected folder.  A STORE that
        # matches no message is a conformant ``OK`` no-op (RFC 3501), so we
        # must guard against a stale UID before issuing any destructive
        # command — otherwise the caller would treat a no-op as success.
        if not self.search_uids(f"UID {uid}"):
            raise ImapMessageNotFoundError(
                f"UID {uid} not found in the selected folder (stale UID)"
            )

        # Mark the message as deleted.
        status, _ = self._imap.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")
        if status != "OK":
            raise ImapError(
                f"UID STORE +FLAGS (\\Deleted) for UID {uid} failed: {status}"
            )

        # Expunge to remove flagged messages.
        status, _ = self._imap.expunge()
        if status != "OK":
            raise ImapError(f"EXPUNGE failed: {status}")

    def move_message(self, uid: int, dest_folder: str) -> None:
        """Copy *uid* to *dest_folder* then delete the original.

        Uses IMAP ``UID COPY`` to copy the message, then calls
        :meth:`delete_message` to mark the original ``\\Deleted``
        and expunge.  This two-step copy-then-delete approach uses
        only RFC-3501-mandated commands (``COPY`` + ``STORE`` /
        ``EXPUNGE``), avoiding the optional ``MOVE`` extension.

        Raises :class:`ImapError` if not connected or the server
        returns a non-OK status for either the ``UID COPY`` or the
        subsequent deletion.
        """
        if self._imap is None:
            raise ImapError("Not connected")

        # Pre-verify the UID exists in the selected folder.  A COPY that
        # matches no message is a conformant ``OK`` no-op (RFC 3501/4315),
        # so guard against a stale UID before issuing COPY or the
        # subsequent delete — otherwise the caller would treat a no-op as
        # success and lose the local record.
        if not self.search_uids(f"UID {uid}"):
            raise ImapMessageNotFoundError(
                f"UID {uid} not found in the selected folder (stale UID)"
            )

        # Copy to destination.
        status, data = self._imap.uid("COPY", str(uid), dest_folder)
        if status != "OK":
            raise ImapError(f"UID COPY of {uid} to {dest_folder!r} failed: {status}")

        # Defensive hardening: if the server advertises UIDPLUS it returns a
        # ``COPYUID`` response code naming the source UID set.  When present
        # and its source-UID set is empty the COPY affected zero messages —
        # treat it as not-found.  Servers that omit ``COPYUID`` are not
        # regressed (we only raise when it is present and indicates zero).
        if self._copyuid_indicates_empty_source(data):
            raise ImapMessageNotFoundError(
                f"UID {uid} not found in the selected folder (stale UID); "
                "COPYUID reported zero source messages"
            )

        # Delete the original from the source mailbox.
        self.delete_message(uid)

    def delete_messages(self, uids: list[int]) -> None:
        """Mark a whole UID set ``\\Deleted`` and expunge, in chunks.

        Processes *uids* in chunks of :data:`_BATCH_UID_CHUNK`, issuing a
        single ``UID STORE +FLAGS (\\Deleted)`` over the comma-joined UID
        set followed by **one** ``EXPUNGE`` per chunk.  A 518-mail delete
        therefore costs at most six round-trip pairs instead of one per
        message.  An empty list is a no-op (no IMAP calls).

        Raises :class:`ImapError` if not connected or the server returns
        a non-OK status for any ``UID STORE`` or ``EXPUNGE``.
        """
        if not uids:
            return
        if self._imap is None:
            raise ImapError("Not connected")

        for start in range(0, len(uids), _BATCH_UID_CHUNK):
            chunk = uids[start : start + _BATCH_UID_CHUNK]
            uid_set = ",".join(str(uid) for uid in chunk)

            status, _ = self._imap.uid("STORE", uid_set, "+FLAGS", "(\\Deleted)")
            if status != "OK":
                raise ImapError(
                    f"UID STORE +FLAGS (\\Deleted) for UID set {uid_set!r} "
                    f"failed: {status}"
                )

            status, _ = self._imap.expunge()
            if status != "OK":
                raise ImapError(f"EXPUNGE failed: {status}")

    def move_messages(self, uids: list[int], dest_folder: str) -> None:
        """Copy a whole UID set to *dest_folder* then delete the originals.

        Processes *uids* in chunks of :data:`_BATCH_UID_CHUNK`: a single
        ``UID COPY`` over the comma-joined UID set to *dest_folder*,
        followed by a batched :meth:`delete_messages` of that same set.
        An empty list is a no-op (no IMAP calls).

        Raises :class:`ImapError` if not connected or the server returns
        a non-OK status for any ``UID COPY`` or the subsequent deletion.
        """
        if not uids:
            return
        if self._imap is None:
            raise ImapError("Not connected")

        for start in range(0, len(uids), _BATCH_UID_CHUNK):
            chunk = uids[start : start + _BATCH_UID_CHUNK]
            uid_set = ",".join(str(uid) for uid in chunk)

            status, _ = self._imap.uid("COPY", uid_set, dest_folder)
            if status != "OK":
                raise ImapError(
                    f"UID COPY of {uid_set!r} to {dest_folder!r} failed: {status}"
                )

            self.delete_messages(chunk)

    @staticmethod
    def _copyuid_indicates_empty_source(data: Any) -> bool:
        """Return ``True`` when a COPY response carries an empty ``COPYUID``.

        Inspects the ``UID COPY`` response data for a ``COPYUID`` response
        code (RFC 4315: ``COPYUID <uidvalidity> <source-set> <dest-set>``).
        Returns ``True`` only when ``COPYUID`` is present AND its source-UID
        set is empty (zero messages copied).  Returns ``False`` when no
        ``COPYUID`` is present, so servers without UIDPLUS are not regressed.
        """
        if not data:
            return False
        for item in data:
            if isinstance(item, bytes):
                text = item.decode("utf-8", errors="replace")
            elif isinstance(item, str):
                text = item
            else:
                continue
            match = re.search(r"COPYUID\s+\d+\s+(\S*)", text)
            if match is None:
                continue
            source_set = match.group(1).strip()
            return source_set == ""
        return False

    @staticmethod
    def _parse_uid_from_fetch_header(header: bytes) -> int | None:
        """Extract the UID from a FETCH response header line.

        Typical format: ``b'1 (UID 42)'`` or ``b'1 (UID 42 BODY[] {5}'``.
        """
        try:
            text = header.decode("utf-8", errors="replace")
        except AttributeError:
            return None
        # Find "(UID " ... ")"
        start = text.find("(UID ")
        if start < 0:
            return None
        start += 5  # len("(UID ")
        end = text.find(" ", start)
        if end < 0:
            end = text.find(")", start)
            if end < 0:
                return None
        try:
            return int(text[start:end].rstrip(")"))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_uid_from_fetch_trailer(item: bytes) -> int | None:
        """Extract the UID from a trailing bare-bytes FETCH item.

        Exchange / Office365 returns the UID after the body literal as a
        separate bare-``bytes`` item, e.g. ``b" UID 10780)"``.  Tolerates
        a leading space and a trailing ``)``.
        """
        try:
            text = item.decode("utf-8", errors="replace")
        except AttributeError:
            return None
        match = re.search(r"UID\s+(\d+)", text)
        if match is None:
            return None
        try:
            return int(match.group(1))
        except (ValueError, TypeError):
            return None
