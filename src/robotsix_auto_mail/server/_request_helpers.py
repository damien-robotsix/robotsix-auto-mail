"""Shared request-handling infrastructure for the board server.

This module hosts the cross-cutting request helpers that more than one
endpoint group needs, relocated here from ``_action_mixin`` so the
composition-era service modules and the remaining legacy mixins can share a
single implementation.

Migration recipe (composition foundation)
------------------------------------------
``BoardHandler`` is being converted from a 15-way mixin inheritance
(``BoardHandler(..., BaseHTTPRequestHandler)``) into composition.  Each mixin
becomes a stateless *service* (see :mod:`._services`).  Every service
receives, per request, a *context* — the running ``BoardHandler`` instance,
typed structurally as
:class:`robotsix_auto_mail.server._board_handler_protocol.RequestContext`.
The context exposes:

* the shared HTTP infrastructure (``_send_response``, ``_redirect``,
  ``_not_found``, ``_bad_request``, ``_problem``, ``_serve_json``,
  ``_require_imap_configured``, ``_validate_archive_path``);
* the injected dependencies (``db_path``, ``mail_config``, ``accounts``);
* the per-request transport (``path``, ``headers``, ``rfile``); and
* the per-request state (``_current_account_id``, ``_aggregate``,
  ``_account_cookie``) that must stay on the handler.

To migrate one mixin:

1. Create ``_<name>_service.py`` with a :class:`._services.Service` subclass;
   move the endpoint methods onto it, taking ``ctx: RequestContext`` as their
   first argument in place of ``self`` for handler-owned state (genuine
   service state — none today — stays on the instance).
2. Register the service in :class:`._services.ServiceContainer` so it is built
   once per :func:`~robotsix_auto_mail.server.handlers.make_board_handler`
   call with the injected dependencies.
3. Rewire ``do_GET``/``do_POST`` in ``handlers.py`` to look the service up on
   ``self._services`` and call it with ``self`` as the context.
4. Port the mixin's ``_FakeHandler`` unit tests to instantiate the service
   directly and pass a stub context.
5. Delete the mixin module and drop it from ``BoardHandler``'s bases.

Body parsing (:func:`parse_request_body`) and the shared POST skeleton
(:func:`handle_post_action`) live here — rather than on any one mixin — so
those steps are order-independent: a mixin can be migrated before or after the
others without depending on ``_action_mixin``.
"""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

from robotsix_auto_mail.server._constants import _is_safe_redirect_path, _with_db

if TYPE_CHECKING:
    from robotsix_auto_mail.server._board_handler_protocol import RequestContext


def _json_field_value(data: dict[str, Any], field: str) -> str:
    """Return *field* from *data* coerced to ``str``.

    ``None`` / missing keys yield the empty string so that
    downstream validation can produce a clear error message
    rather than a spurious ``"None"`` literal.
    """
    val = data.get(field)
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    return str(val)


def parse_request_body(
    ctx: RequestContext, *fields: str, no_strip: frozenset[str] = frozenset()
) -> dict[str, str]:
    """Parse the request body as URL-encoded form data.

    Returns a dict mapping each requested *field* name to its
    first value.  Values are stripped of leading/trailing
    whitespace *unless* the field name appears in *no_strip*.

    When form parsing yields only empty values, falls back to
    JSON parsing so that clients sending ``Content-Type:
    application/json`` receive the same behaviour.
    """
    content_length = int(ctx.headers.get("Content-Length", 0))
    raw = ctx.rfile.read(content_length).decode("utf-8")
    parsed = parse_qs(raw)
    result = {
        field: (
            (parsed.get(field) or [""])[0].strip()
            if field not in no_strip
            else (parsed.get(field) or [""])[0]
        )
        for field in fields
    }

    # JSON fallback: when form parsing yields only empty values,
    # try to parse the body as JSON.
    if all(v == "" for v in result.values()):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(data, dict):
                result = {
                    field: (
                        _json_field_value(data, field).strip()
                        if field not in no_strip
                        else _json_field_value(data, field)
                    )
                    for field in fields
                }
    return result


def handle_post_action(
    ctx: RequestContext,
    *fields: str,
    action: Any,
    no_strip: frozenset[str] = frozenset(),
    cross_account: bool = False,
) -> None:
    """Shared POST handler skeleton.

    1. Parses the request body for the declared *fields*.
    2. Validates ``message_id`` (returns 400 if missing).
    3. Opens a read-only DB connection, looks up the record,
       and returns 404 if absent.
    4. Delegates to *action(conn, record, redirect_to,
       \\*\\*extra_fields)* for the handler-specific logic.
       When *action* returns ``False`` the redirect is skipped
       (the callback already sent a response).
    5. Closes the connection and performs a safe redirect.

    When *cross_account* is ``True`` and the record is not found in
    the currently-selected account's DB, every other configured
    account is searched.  This lets ids that carry no ``?account=``
    hint — notably the synthetic ``<compose-...@robotsix-auto-mail>``
    ids returned by ``/compose-draft`` — resolve to whichever
    account actually holds the record instead of 404-ing against the
    default (first) account.  For the matching account ``ctx.db_path``
    and ``ctx.mail_config`` are rebound for the duration of *action*
    so IMAP work targets the owning mailbox.
    """
    from robotsix_auto_mail.db import get_record_by_message_id

    f = parse_request_body(ctx, *fields, no_strip=no_strip)
    message_id = f.get("message_id", "")
    redirect_to = f.get("redirect_to", "")

    if not message_id:
        content_type = ctx.headers.get("Content-Type", "")
        if isinstance(content_type, str) and "application/json" in content_type:
            ctx._bad_request("Malformed JSON body")
        else:
            ctx._bad_request("Missing message_id")
        return

    extra = {k: v for k, v in f.items() if k not in ("message_id", "redirect_to")}

    # Resolve the account that owns this record.  The currently-selected
    # account is tried first; other accounts are appended only when
    # *cross_account* fallback is enabled.
    owners: list[tuple[str, Any]] = [(ctx.db_path, ctx.mail_config)]
    if cross_account and ctx.accounts is not None:
        owners.extend(
            (account.config.db_path, account.config)
            for account in ctx.accounts.accounts
            if account.config.db_path != ctx.db_path
        )

    for db_path, mail_config in owners:
        with _with_db(db_path) as conn:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                continue

            saved_db: str = ctx.db_path
            saved_config: object | None = ctx.mail_config
            ctx.db_path = db_path
            ctx.mail_config = mail_config
            try:
                if action(conn, record, redirect_to, **extra) is False:
                    return
            finally:
                ctx.db_path = saved_db
                ctx.mail_config = saved_config

        if redirect_to and _is_safe_redirect_path(redirect_to):
            ctx._redirect(redirect_to, code=302)
        else:
            ctx._redirect("/board", code=302)
        return

    ctx._not_found()
