"""IMAP client package.

The implementation is split across internal submodules:

- ``errors`` — the IMAP exception hierarchy.
- ``utils`` — pure modified-UTF-7 encoding helpers and mailbox quoting.
- ``mailbox`` — the ``MailboxInfo`` model plus LIST-parsing and
  cross-folder / fallback UID resolution helpers.
- ``client`` — the ``ImapClient`` class and its connection constants.

This module re-exports the public and previously-importable symbols so
``from robotsix_auto_mail.imap import ...`` keeps working unchanged.
"""

from __future__ import annotations

# ``build_token_provider`` is re-exported BEFORE ``.client`` is imported so
# the name is bound on the (partially-initialised) package when
# ``client.py`` resolves it via a function-local
# ``from robotsix_auto_mail.imap import build_token_provider``.  Tests patch
# this package attribute (``robotsix_auto_mail.imap.build_token_provider``).
from robotsix_auto_mail.oauth2 import build_token_provider as build_token_provider

from ._protocol import _ProtocolClient as _ProtocolClient
from ._protocol import build_xoauth2_response as build_xoauth2_response
from .client import _BATCH_UID_CHUNK as _BATCH_UID_CHUNK
from .client import ImapClient as ImapClient
from .errors import ImapAuthError as ImapAuthError
from .errors import ImapConnectionError as ImapConnectionError
from .errors import ImapError as ImapError
from .errors import ImapMessageNotFoundError as ImapMessageNotFoundError
from .errors import ImapTlsError as ImapTlsError
from .mailbox import MailboxInfo as MailboxInfo
from .mailbox import _is_waste_folder as _is_waste_folder
from .mailbox import _parse_list_line as _parse_list_line
from .mailbox import cross_folder_resolve as cross_folder_resolve
from .mailbox import is_special_use as is_special_use
from .mailbox import resolve_uid_with_fallback as resolve_uid_with_fallback
from .utils import imap_utf7_decode as imap_utf7_decode
from .utils import imap_utf7_encode as imap_utf7_encode
