"""IMAP client built on stdlib ``imaplib``.

Provides ``ImapClient`` — a context manager that connects to a real IMAP
server, negotiates TLS, authenticates, and exposes mailbox inspection,
message retrieval, and message mutation primitives:

- Server metadata: ``server_greeting``, ``capabilities``
- Mailbox management: ``list_folders()``, ``select_folder()``,
  ``select_folder_and_uidvalidity()``, ``create_folder()``
- Message retrieval: ``search_uids()``, ``fetch_messages()``
- Message mutation: ``delete_message()``, ``move_message()``,
  ``delete_messages()``, ``move_messages()``

Depends only on ``MailConfig`` from ``robotsix_auto_mail.config`` and the
Python standard library (``imaplib``, ``ssl``).
"""

from __future__ import annotations

import contextlib
import imaplib
import re
import ssl
from collections.abc import Iterator
from typing import Any

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap import _ProtocolClient, build_xoauth2_response

from .errors import (
    ImapAuthError,
    ImapConnectionError,
    ImapError,
    ImapMessageNotFoundError,
    ImapTlsError,
)
from .mailbox import MailboxInfo, _parse_list_line  # lgtm[py/unsafe-cyclic-import]
from .utils import _encode_mailbox, _is_gmail_host

# Store a reference to IMAP4.error *before* any mocking can replace
# IMAP4 and turn ``IMAP4.error`` into a MagicMock attribute.  Using
# this reference in except clauses keeps tests reliable.
_IMAP4_ERROR = imaplib.IMAP4.error

# Maximum number of UIDs packed into a single ``UID STORE`` / ``UID COPY``
# round-trip by the batched delete/move primitives.  A 518-mail column then
# costs at most six round-trip pairs instead of one per message.
_BATCH_UID_CHUNK = 100

# Socket timeout (seconds) for IMAP connections.  stdlib ``imaplib`` opens
# sockets with NO timeout by default, so a stalled server read blocks the
# calling thread forever — which silently wedges background batch
# delete/archive workers (their ``finally`` that clears ``batch_op:state``
# never runs).  A generous timeout turns a stall into a raised
# ``TimeoutError`` (caught like any other IMAP failure) instead of a hang.
_IMAP_TIMEOUT_SECONDS = 60

# Gmail rejects a normal account password over IMAP; only an App Password
# (with 2-Step Verification enabled) or OAuth2 works.  Appended to the auth
# error when a plain-login attempt against a Gmail host fails, so the user is
# pointed at the right credential instead of the opaque server message.
_GMAIL_AUTH_HINT = (
    "\nGmail does not accept your normal account password over IMAP. Enable "
    "IMAP in Gmail settings, turn on 2-Step Verification, then create a "
    "16-character App Password at https://myaccount.google.com/apppasswords "
    "and use that as the password (or configure OAuth2)."
)


# ---------------------------------------------------------------------------
# ImapClient
# ---------------------------------------------------------------------------


class ImapClient(_ProtocolClient):
    """Context-managed IMAP client.

    Constructor accepts a ``MailConfig`` and extracts only the IMAP-relevant
    fields (``imap_host``, ``imap_port``, ``imap_tls_mode``, ``username``,
    ``password``).  The SMTP fields are never referenced.

    Public API (beyond the context-manager protocol ``__enter__`` /
    ``__exit__``):

    - Server metadata: ``server_greeting``, ``capabilities``
    - Mailbox management: ``list_folders()``, ``select_folder()``,
      ``select_folder_and_uidvalidity()``, ``create_folder()``
    - Message retrieval: ``search_uids()``, ``fetch_messages()``
    - Message mutation: ``delete_message()``, ``move_message()``,
      ``delete_messages()``, ``move_messages()``

    Typical usage::

        cfg = load_accounts().default.config
        with ImapClient(cfg) as client:
            folders = client.list_folders()
            count = client.select_folder("INBOX")
    """

    def __init__(self, config: MailConfig) -> None:
        """Initialise the IMAP client from a ``MailConfig``.

        Extracts IMAP connection parameters (host, port, TLS mode,
        credentials) and resolves the OAuth2 token provider (if configured)
        via the package-level ``build_token_provider`` so that test patches
        intercept at the package rather than module level.
        """
        # Resolve ``build_token_provider`` through the package at call time so
        # that ``mock.patch("robotsix_auto_mail.imap.build_token_provider")``
        # intercepts this construction.  A module-level import here would bind
        # the function on ``client`` and bypass the package-level patch.
        from robotsix_auto_mail.imap import build_token_provider

        super().__init__(
            host=config.imap_host,
            port=config.imap_port,
            tls_mode=config.imap_tls_mode,
            username=config.username,
            password=config.password,
            oauth2_token=config.oauth2_token,
            config=config,
            build_token_provider_fn=build_token_provider,
        )
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
            with contextlib.suppress(Exception):
                # Connection may already be dead - best-effort close.
                self._imap.logout()
        # In case logout() left the socket dangling, close it ourselves.
        self._close_socket()

    # -- connection helpers ------------------------------------------------

    def _connect_direct_tls(self) -> None:
        """Open an IMAP-over-TLS (port 993) connection via ``IMAP4_SSL``.

        Wraps ``OSError`` / ``IMAP4.error`` in ``ImapConnectionError`` so
        callers get a uniform exception shape regardless of TLS mode.
        """
        ctx = ssl.create_default_context()
        try:
            self._imap = imaplib.IMAP4_SSL(
                self._host,
                self._port,
                ssl_context=ctx,
                timeout=_IMAP_TIMEOUT_SECONDS,
            )
        except (OSError, _IMAP4_ERROR) as exc:
            raise ImapConnectionError(
                f"Direct-TLS connection to {self._host}:{self._port} failed: {exc}"
            ) from exc

    def _connect_starttls(self) -> None:
        """Open a plain IMAP connection then upgrade to TLS via ``STARTTLS``.

        The initial plain socket is immediately upgraded — the ``lgtm``
        suppression acknowledges that this is the intended ``tls_mode ==
        "starttls"`` path, not an unencrypted session.  Failures during
        STARTTLS negotiation close the underlying socket and raise
        ``ImapTlsError``.
        """
        # 1. Plain connection
        try:
            # Plain socket here is the intended tls_mode == "starttls" path; it
            # is immediately upgraded via starttls() below, not an unencrypted
            # session.
            # lgtm[py/clear-text-transmission-sensitive-data]
            self._imap = imaplib.IMAP4(
                self._host, self._port, timeout=_IMAP_TIMEOUT_SECONDS
            )
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
        """Open an unencrypted IMAP connection (``tls_mode == "none"``).

        The plaintext socket is operator-selected, not a silent downgrade —
        the ``lgtm`` suppression documents this.  Connection failures raise
        ``ImapConnectionError``.
        """
        try:
            # Plaintext IMAP is the operator-selected tls_mode == "none"
            # configuration (a supported option), not a silent downgrade.
            # lgtm[py/clear-text-transmission-sensitive-data]
            self._imap = imaplib.IMAP4(
                self._host, self._port, timeout=_IMAP_TIMEOUT_SECONDS
            )
        except (OSError, _IMAP4_ERROR) as exc:
            raise ImapConnectionError(
                f"Plain (no-TLS) connection to {self._host}:{self._port} failed: {exc}"
            ) from exc

    def _authenticate(self) -> None:
        """Authenticate to the connected IMAP server.

        Chooses XOAUTH2 (when a token provider is configured or a static
        token is present) or plain password login.  On XOAUTH2 failure when
        MSAL manages the token, performs a force-refresh retry: reconnects,
        refreshes the token, and attempts XOAUTH2 once more.  All failures
        are wrapped in ``ImapAuthError`` with a Gmail-specific hint appended
        when appropriate.
        """
        if self._imap is None:
            raise RuntimeError("_authenticate() called before _connect_*()")
        self._xoauth2_challenge = b""  # reset on each attempt
        if self._token_provider is not None:
            self._oauth2_token = self._token_provider()
        used_oauth2 = self._token_provider is not None or bool(self._oauth2_token)
        try:
            if used_oauth2:
                # The XOAUTH2 token is passed to imaplib for the SASL
                # handshake only; it is never logged or stored in clear text.
                # lgtm[py/clear-text-storage-sensitive-data]
                self._imap.authenticate("XOAUTH2", self._imap_xoauth2_cb)
            else:
                # The username/password are passed to imaplib's LOGIN command
                # only; they are never logged or stored in clear text.
                # lgtm[py/clear-text-storage-sensitive-data]
                self._imap.login(self._username, self._password)
        except _IMAP4_ERROR as exc:
            # Force-refresh retry: only when MSAL manages the token
            # (self._msal_config is set) — static oauth2_token has no
            # refresh mechanism.
            if self._msal_config is not None:
                self._oauth2_token = self._imap_force_refresh()
                # Reconnect for a clean NOT AUTHENTICATED state
                self._close_socket()
                self._dispatch_tls()
                self._xoauth2_challenge = b""
                try:
                    self._imap.authenticate("XOAUTH2", self._imap_xoauth2_cb)
                    return  # retry succeeded
                except _IMAP4_ERROR as exc2:
                    from robotsix_auto_mail.oauth2 import (
                        classify_xoauth2_auth_error,
                    )

                    raise ImapAuthError(
                        classify_xoauth2_auth_error(
                            self._xoauth2_challenge,
                            username=self._username,
                            host=self._host,
                            port=self._port,
                        )
                    ) from exc2
            # Non-MSAL fallthrough — original error message
            message = (
                f"Authentication failed for user {self._username!r} "
                f"on {self._host}:{self._port}: {exc}"
            )
            # A plain-password rejection from Gmail almost always means the
            # user supplied their normal password instead of an App Password.
            if not used_oauth2 and _is_gmail_host(self._host):
                message += _GMAIL_AUTH_HINT
            raise ImapAuthError(message) from exc

    def _imap_force_refresh(self) -> str:
        """Force-refresh the MSAL token, passing any CAE claims extracted
        from the server's XOAUTH2 challenge.  Only called when
        ``self._msal_config`` is set.
        """
        from robotsix_auto_mail.oauth2 import force_refresh_token

        return force_refresh_token(self._msal_config, self._xoauth2_challenge)  # type: ignore[arg-type]

    def _imap_xoauth2_cb(self, challenge: bytes) -> bytes | None:
        """SASL XOAUTH2 callback for ``imaplib.IMAP4.authenticate()``.

        On the initial (empty) challenge returns the XOAUTH2 response
        string.  On any non-empty challenge (server-side error) saves
        it for retry analysis and cancels with ``None``.
        """
        if challenge:
            self._xoauth2_challenge = challenge
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
        except Exception:  # noqa: S110  # nosec B110  # lgtm[py/empty-except]
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
        status, data = self._imap.select(_encode_mailbox(name))
        if status != "OK":
            raise ImapError(f"SELECT '{name}' failed: {status}")
        if data and data[0]:
            try:
                return int(data[0])
            except ValueError, TypeError:
                return 0
        return 0

    def select_folder_and_uidvalidity(self, name: str) -> tuple[int, int | None]:
        """Select a mailbox and return ``(message_count, uidvalidity)``.

        ``UIDVALIDITY`` is the server's UID-namespace generation for the
        mailbox (RFC 3501 §2.3.1.1). It only changes when the server
        renumbers UIDs (mailbox recreated/restored, some server
        maintenance) — and when it does, any stored UID watermark from the
        old namespace is meaningless. Callers persist this value and reset
        their UID watermark on a mismatch so incremental fetch keeps working.

        Returns the ``UIDVALIDITY`` as an ``int`` when the server advertises
        it on ``SELECT`` (all RFC-3501 servers do), or ``None`` when it is
        absent or unparseable — in which case callers must leave their UID
        watermark untouched rather than guess.

        Args:
            name: Mailbox name (e.g. ``"INBOX"``).

        Raises:
            ImapError: If the client is not connected or the ``SELECT``
                returns a non-OK response.
        """
        count = self.select_folder(name)
        if self._imap is None:  # pragma: no cover - select_folder already guards
            raise ImapError("Not connected")
        _typ, data = self._imap.response("UIDVALIDITY")
        uidvalidity: int | None = None
        if data and data[0]:
            try:
                uidvalidity = int(data[0])
            except ValueError, TypeError:
                uidvalidity = None
        return count, uidvalidity

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
        status, _data = self._imap.create(_encode_mailbox(name))
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
        with contextlib.suppress(Exception):
            self._imap.subscribe(_encode_mailbox(name))

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
        except AttributeError, LookupError:
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
        status, data = self._imap.uid("COPY", str(uid), _encode_mailbox(dest_folder))
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

    def _filter_valid_uids(self, uids: list[int]) -> Iterator[tuple[str, list[int]]]:
        """Yield ``(valid_set_str, valid_uids_list)`` for each batch of *uids*.

        Chunks *uids* into groups of :data:`_BATCH_UID_CHUNK` and performs a
        ``UID SEARCH`` pre-verification on each chunk to filter out stale UIDs
        that are no longer present in the selected folder.  Chunks where every
        UID is stale are silently skipped.
        """
        for start in range(0, len(uids), _BATCH_UID_CHUNK):
            chunk = uids[start : start + _BATCH_UID_CHUNK]
            uid_set = ",".join(str(uid) for uid in chunk)
            existing = set(self.search_uids(f"UID {uid_set}"))
            valid_uids = [uid for uid in chunk if uid in existing]
            if not valid_uids:
                continue
            valid_set = ",".join(str(uid) for uid in valid_uids)
            yield valid_set, valid_uids

    def delete_messages(self, uids: list[int]) -> None:
        """Mark a whole UID set ``\\Deleted`` and expunge, in chunks.

        Processes *uids* in chunks of :data:`_BATCH_UID_CHUNK`, issuing a
        single ``UID STORE +FLAGS (\\Deleted)`` over the comma-joined UID
        set followed by **one** ``EXPUNGE`` per chunk.  A 518-mail delete
        therefore costs at most six round-trip pairs instead of one per
        message.  An empty list is a no-op (no IMAP calls).

        Stale UIDs (those no longer present in the selected folder) are
        silently filtered out via a ``UID SEARCH`` pre-verification before
        the destructive ``UID STORE`` is issued.  A chunk where every UID
        is stale is skipped entirely.

        Raises :class:`ImapError` if not connected or the server returns
        a non-OK status for any ``UID STORE`` or ``EXPUNGE``.
        """
        if not uids:
            return
        if self._imap is None:
            raise ImapError("Not connected")

        for valid_set, _valid_uids in self._filter_valid_uids(uids):
            status, _ = self._imap.uid("STORE", valid_set, "+FLAGS", "(\\Deleted)")
            if status != "OK":
                raise ImapError(
                    f"UID STORE +FLAGS (\\Deleted) for UID set "
                    f"{valid_set!r} failed: {status}"
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

        Stale UIDs are silently filtered out via a ``UID SEARCH``
        pre-verification before ``UID COPY`` is issued.  After COPY,
        the ``COPYUID`` response code (RFC 4315) is inspected — if it
        indicates zero source messages were copied, deletion of the
        originals is skipped.

        Raises :class:`ImapError` if not connected or the server returns
        a non-OK status for any ``UID COPY`` or the subsequent deletion.
        """
        if not uids:
            return
        if self._imap is None:
            raise ImapError("Not connected")

        for valid_set, valid_uids in self._filter_valid_uids(uids):
            status, data = self._imap.uid(
                "COPY", valid_set, _encode_mailbox(dest_folder)
            )
            if status != "OK":
                raise ImapError(
                    f"UID COPY of {valid_set!r} to {dest_folder!r} failed: {status}"
                )

            # Defensive hardening: if the server advertises UIDPLUS it
            # returns a ``COPYUID`` response code.  When present and its
            # source-UID set is empty the COPY affected zero messages —
            # skip deletion of originals.
            if self._copyuid_indicates_empty_source(data):
                continue

            self.delete_messages(valid_uids)

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
        except ValueError, TypeError:
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
        except ValueError, TypeError:
            return None
