"""Board rendering functions for the board server."""

from __future__ import annotations

import contextlib
import html
import json
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import quote

from robotsix_board import render_board

from robotsix_auto_mail._constants import (
    _BATCH_OP_STATE_KEY,
    _TRIAGE_RUN_STATE_KEY,
)
from robotsix_auto_mail.config import (
    DEFAULT_ARCHIVE_ROOT,
    MailAccountsConfig,
)
from robotsix_auto_mail.db import MailRecord, list_records
from robotsix_auto_mail.oauth2 import MICROSOFT_PROVIDER
from robotsix_auto_mail.server._constants import (
    _BOARD_COLUMNS,
    BATCH_OP_VERB_LABELS,
    BATCH_OP_VERBS,
    _with_db,
)
from robotsix_auto_mail.server.adapters import _NonEmptyColumnsAdapter
from robotsix_auto_mail.server.board_adapter import MailBoardAdapter
from robotsix_auto_mail.triage import (
    HUMAN_TRIAGE,
    INBOX,
    TO_ARCHIVE,
    TRIAGE_ACTION_LABELS,
    TRIAGE_ACTION_ORDER,
    TriageDecision,
    get_archive_subfolder,
    list_triage_decisions,
)


def _render_board_columns(
    adapter: MailBoardAdapter, cards: Mapping[str, list[MailRecord]]
) -> str:
    """Return the inner ``.board-column`` markup produced by ``render_board``.

    ``render_board`` wraps the columns in a ``<div id="board" class="board">``
    element and appends a ``#drawer`` shell that auto-mail does not use (it
    has its own ``.side-panel``).  Both the ``/board`` page shell and the
    ``/board-content`` refresh endpoint expect just the inner column markup
    — to be injected into auto-mail's own ``.board`` wrapper — so this
    helper strips the library's outer wrapper and drawer.  Returns the
    empty-board placeholder when no column is populated.
    """
    ordered = [key for key, _label in adapter.columns() if key in cards]
    if not ordered:
        return '<div class="empty-board">No mail yet.</div>'
    full = render_board(cast("Any", _NonEmptyColumnsAdapter(adapter, ordered)), cards)
    open_tag = '<div id="board" class="board">'
    drawer = (
        '<div id="drawer" class="drawer hidden">'
        '<div class="drawer-content"></div>'
        "</div>"
    )
    inner = full
    if inner.startswith(open_tag):
        inner = inner[len(open_tag) :]
    drawer_idx = inner.rfind(drawer)
    if drawer_idx != -1:
        inner = inner[:drawer_idx]
    inner = inner.rstrip()
    if inner.endswith("</div>"):
        inner = inner[: -len("</div>")]
    return inner.strip("\n")


def _gather_account_board_data(
    db_path: str,
    archive_root: str = DEFAULT_ARCHIVE_ROOT,
) -> dict[str, Any]:
    """Read one account's DB and return the raw structures for board building.

    Returns a dict with keys: ``triage_running``, ``batch_op``,
    ``triage_by_mid``, ``column_buckets``, ``proposals``,
    ``archive_subfolders``, ``folder_exists``, ``unsubscribe_suggestions``,
    ``record_notes``.

    This is the DB-reading half of :func:`_build_board_content`, extracted
    so the global board can call it per-account.
    """
    from robotsix_auto_mail.db import get_watermark
    from robotsix_auto_mail.db.queries import get_account_health

    with _with_db(db_path, skip_migrations=True) as conn:
        # Read account health so the board can display a red banner on failures.
        health = get_account_health(conn)
        # Check whether the triage agent is currently running so the
        # board can show a visual indicator and disable the button.
        triage_running = get_watermark(conn, _TRIAGE_RUN_STATE_KEY) == "running"

        # Parse the batch-op watermark (delete/archive progress).  The
        # value is ``"idle"``/``None`` when no batch op is running, else a
        # JSON ``{"op", "done", "total"}`` progress payload.
        batch_raw = get_watermark(conn, _BATCH_OP_STATE_KEY)
        batch_op: dict[str, Any] | None = None
        if batch_raw is not None and batch_raw != "idle":
            try:
                parsed = json.loads(batch_raw)
            except json.JSONDecodeError, TypeError:
                parsed = None
            if isinstance(parsed, dict):
                batch_op = {
                    "op": parsed.get("op"),
                    "done": parsed.get("done"),
                    "total": parsed.get("total"),
                }
            else:
                # A bare "running" sentinel (set by the handler before the
                # worker writes its first JSON payload) still counts as a
                # running batch op with as-yet-unknown counts.
                batch_op = {"op": None, "done": None, "total": None}

        # Gather every record and every triage decision once.
        all_records = list_records(conn)
        triage_by_mid: dict[str, TriageDecision] = {
            decision.message_id: decision for decision in list_triage_decisions(conn)
        }
        # Bucket records into columns by their triage-decision action.
        # Untriaged records land in the ``"INBOX"`` column.
        column_buckets: dict[str, list[MailRecord]] = {
            action: [] for action in _BOARD_COLUMNS
        }
        for record in all_records:
            decision = triage_by_mid.get(record.message_id)
            if decision is not None:
                column = decision.action
                # Guard: an unrecognised action lands in HUMAN_TRIAGE.
                if column not in column_buckets:
                    column = HUMAN_TRIAGE
            else:
                column = INBOX
            column_buckets[column].append(record)

        # -- archive proposal context -------------------------------------
        # Read the archive_structure watermark to know which folders exist.
        archive_raw = get_watermark(conn, "archive_structure")
        existing_folders: set[str] = set()
        delimiter: str = "/"
        effective_root: str = archive_root
        if archive_raw is not None:
            try:
                data = json.loads(archive_raw)
                if isinstance(data, list):
                    # Old format: bare list of folder names.
                    existing_folders = set(data)
                    delimiter = "/"
                    effective_root = data[0] if data else archive_root
                else:
                    # New format: {"delimiter": ..., "folders": [...]}.
                    existing_folders = set(data["folders"])
                    delimiter = data.get("delimiter", "/")
                    effective_root = (
                        data["folders"][0] if data["folders"] else archive_root
                    )
            except json.JSONDecodeError, TypeError, KeyError:
                # Malformed archive_structure watermark — keep the empty
                # folder set / "/" delimiter / archive_root defaults set above.
                pass

        # Compute effective subfolder for each TO_ARCHIVE record.
        archive_subfolders: dict[str, str] = {}
        folder_exists: dict[str, bool] = {}
        for record in column_buckets.get(TO_ARCHIVE, []):
            subfolder = get_archive_subfolder(conn, record.message_id, record)
            archive_subfolders[record.message_id] = subfolder
            if subfolder:
                translated = subfolder.replace("/", delimiter)
                full_path = f"{effective_root}{delimiter}{translated}"
            else:
                full_path = effective_root
            folder_exists[record.message_id] = full_path in existing_folders

        # Order TO_ARCHIVE cards by destination so the board JS renders
        # contiguous per-folder groups (each with an "Archive these" button).
        # ``list.sort`` is stable, so the prior within-folder order is kept.
        to_archive_bucket = column_buckets.get(TO_ARCHIVE)
        if to_archive_bucket:
            to_archive_bucket.sort(
                key=lambda r: archive_subfolders.get(r.message_id, "")
            )

        # -- unsubscribe suggestions for TO_DELETE column -----------------
        suggestions_raw = get_watermark(conn, "unsubscribe_suggestions")
        unsubscribe_suggestions: dict[str, dict[str, Any]] = {}
        if suggestions_raw is not None:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                # Malformed unsubscribe_suggestions watermark — leave the
                # empty suggestions dict initialised above.
                unsubscribe_suggestions = json.loads(suggestions_raw)

        # Build record_notes map for notes indicators.
        record_notes: dict[str, str] = {
            r.message_id: r.notes for r in all_records if r.notes
        }

        # Existing archive subfolders (relative to the root) for the per-card
        # override dropdown — strip the ``<effective_root><delimiter>`` prefix
        # off each managed folder, dropping the root itself.
        _root_prefix = f"{effective_root}{delimiter}"
        archive_folders = sorted(
            name[len(_root_prefix) :]
            for name in existing_folders
            if name.startswith(_root_prefix) and name != effective_root
        )

    return {
        "triage_running": triage_running,
        "batch_op": batch_op,
        "health": health,
        "triage_by_mid": triage_by_mid,
        "column_buckets": column_buckets,
        "archive_subfolders": archive_subfolders,
        "archive_folders": archive_folders,
        "folder_exists": folder_exists,
        "unsubscribe_suggestions": unsubscribe_suggestions,
        "record_notes": record_notes,
    }


def _build_board_content(
    db_path: str,
    archive_root: str = DEFAULT_ARCHIVE_ROOT,
    *,
    account_id: str = "main",
    config_failures: tuple[tuple[str, str], ...] = (),
    mail_config: object | None = None,
) -> dict[str, Any]:
    """Return ``{"columns_html": …, "triage_running": …}``.

    Opens a fresh database connection, gathers every mail record and
    buckets them into kanban columns based on each record's triage
    decision.  Cards with no triage decision land in the ``"INBOX"``
    column.  Renders column and rule-proposal HTML fragments and
    returns them as a plain dict.

    Raises ``Exception`` when the database cannot be opened (the
    caller should catch it and return a 503).
    """
    gathered = _gather_account_board_data(db_path, archive_root=archive_root)

    triage_running = gathered["triage_running"]
    batch_op = gathered["batch_op"]
    health = gathered["health"]
    triage_by_mid: dict[str, TriageDecision] = gathered["triage_by_mid"]
    column_buckets: dict[str, list[MailRecord]] = gathered["column_buckets"]
    archive_subfolders: dict[str, str] = gathered["archive_subfolders"]
    archive_folders: list[str] = gathered["archive_folders"]
    folder_exists: dict[str, bool] = gathered["folder_exists"]
    unsubscribe_suggestions: dict[str, dict[str, Any]] = gathered[
        "unsubscribe_suggestions"
    ]
    record_notes: dict[str, str] = gathered["record_notes"]

    # Build the board adapter — the single source of truth for both the
    # base column/card scaffold (the protocol methods) and auto-mail's
    # custom per-card / per-column widgets, which the library appends via
    # the duck-typed ``card_extra_html`` / ``column_extra_html`` hooks.
    adapter = MailBoardAdapter(
        triage_by_mid={mid: d.action for mid, d in triage_by_mid.items()},
        archive_subfolders=archive_subfolders,
        folder_exists=folder_exists,
        archive_root=archive_root,
        archive_folders=archive_folders,
        unsubscribe_suggestions=unsubscribe_suggestions,
        record_notes=record_notes,
        column_records=column_buckets,
        batch_running=batch_op is not None,
    )
    # auto-mail hides empty columns, so only the populated buckets are
    # handed to ``render_board`` (which renders one column per adapter
    # column).
    cards: dict[str, list[MailRecord]] = {
        action: column_buckets[action]
        for action in _BOARD_COLUMNS
        if column_buckets[action]
    }
    columns_html = _render_board_columns(adapter, cards)

    # Health alerts for the red banner + JS polling.
    health_alerts_html = _health_alerts_html(
        {account_id: health} if health else {},
        account_labels=None,
        config_failures=config_failures,
        account_configs={account_id: mail_config} if mail_config is not None else None,
    )

    return {
        "columns_html": columns_html,
        "triage_running": triage_running,
        "batch_op": batch_op,
        "health": health,
        "health_alerts_html": health_alerts_html,
        "unsubscribe_suggestions": unsubscribe_suggestions,
    }


def _build_global_board_content(
    accounts: MailAccountsConfig,
) -> dict[str, Any]:
    """Aggregate board content across all configured accounts.

    Calls :func:`_gather_account_board_data` for each account, merges the
    per-account column buckets, and builds a single :class:`MailBoardAdapter`
    with the merged context plus ``record_accounts`` / ``account_labels``
    maps so per-card widgets route to the owning account's DB.

    Returns the same JSON shape as :func:`_build_board_content`
    (``columns_html``, ``proposals_html``, ``triage_running``,
    ``unsubscribe_suggestions``).

    Assumption: ``message_id`` is treated as globally unique (RFC 5322).
    Cross-account duplicate message_ids are a known limitation and out of
    scope.
    """
    # Per-account merge accumulators.
    merged_buckets: dict[str, list[MailRecord]] = {
        action: [] for action in _BOARD_COLUMNS
    }
    merged_triage_by_mid: dict[str, str] = {}
    merged_archive_subfolders: dict[str, str] = {}
    merged_folder_exists: dict[str, bool] = {}
    merged_unsubscribe: dict[str, dict[str, Any]] = {}
    merged_record_notes: dict[str, str] = {}
    record_accounts: dict[str, str] = {}
    account_labels: dict[str, str] = {}
    account_health: dict[str, dict[str, Any] | None] = {}
    triage_running: bool = False
    # Aggregate batch-op progress across accounts.  Each account runs its
    # own worker against its own DB; we sum their done/total so the banner
    # shows combined progress for the fanned-out "Delete All".
    batch_running: bool = False
    batch_done: int = 0
    batch_total: int = 0
    batch_verb: str | None = None

    for account in accounts.accounts:
        aid = account.account_id
        label = account.label if account.label else aid
        account_labels[aid] = label

        gathered = _gather_account_board_data(
            account.config.db_path,
            archive_root=account.config.archive_root,
        )

        triage_running = triage_running or gathered["triage_running"]
        account_health[aid] = gathered["health"]

        account_batch = gathered["batch_op"]
        if account_batch is not None:
            batch_running = True
            done = account_batch.get("done")
            total = account_batch.get("total")
            if isinstance(done, int):
                batch_done += done
            if isinstance(total, int):
                batch_total += total
            if account_batch.get("op") and batch_verb is None:
                batch_verb = account_batch["op"]

        # Merge column buckets.
        for action in _BOARD_COLUMNS:
            merged_buckets[action].extend(gathered["column_buckets"][action])

        # Track per-message owning account.
        for recs in gathered["column_buckets"].values():
            for rec in recs:
                record_accounts[rec.message_id] = aid

        # Merge context maps.
        merged_triage_by_mid.update(
            {mid: d.action for mid, d in gathered["triage_by_mid"].items()}
        )
        merged_archive_subfolders.update(gathered["archive_subfolders"])
        merged_folder_exists.update(gathered["folder_exists"])
        merged_unsubscribe.update(gathered["unsubscribe_suggestions"])
        merged_record_notes.update(gathered["record_notes"])

    adapter = MailBoardAdapter(
        triage_by_mid=merged_triage_by_mid,
        archive_subfolders=merged_archive_subfolders,
        folder_exists=merged_folder_exists,
        archive_root=DEFAULT_ARCHIVE_ROOT,  # not used per-card in aggregate mode
        unsubscribe_suggestions=merged_unsubscribe,
        record_notes=merged_record_notes,
        column_records=merged_buckets,
        # Suppress the column-wide Delete-All button while any account's
        # fanned-out batch op is still in flight (the banner takes over).
        batch_running=batch_running,
        record_accounts=record_accounts,
        account_labels=account_labels,
    )

    cards: dict[str, list[MailRecord]] = {
        action: merged_buckets[action]
        for action in _BOARD_COLUMNS
        if merged_buckets[action]
    }
    columns_html = _render_board_columns(adapter, cards)

    batch_op: dict[str, Any] | None = None
    if batch_running:
        batch_op = {"op": batch_verb, "done": batch_done, "total": batch_total}

    health_alerts_html = _health_alerts_html(
        account_health,
        account_labels,
        config_failures=(),
        account_configs={a.account_id: a.config for a in accounts.accounts},
    )

    return {
        "columns_html": columns_html,
        "triage_running": triage_running,
        "unsubscribe_suggestions": merged_unsubscribe,
        "batch_op": batch_op,
        "health_alerts_html": health_alerts_html,
        "account_health": account_health,
    }


def _batch_banner_html(batch_op: dict[str, Any] | None) -> str:
    """Return the ``.batch-banner`` markup for a running batch op, or ``""``.

    Renders the operation verb plus ``done/total`` progress (e.g.
    ``"Deleting mail: 120/518. The board will refresh automatically."``).
    Returns the empty string when *batch_op* is ``None`` (no op running).
    """
    if batch_op is None:
        return ""
    op = batch_op.get("op")
    if isinstance(op, str):
        verb = BATCH_OP_VERB_LABELS.get(op, "Processing")
    else:
        verb = "Processing"
    done = batch_op.get("done")
    total = batch_op.get("total")
    if isinstance(done, int) and isinstance(total, int):
        progress = f": {done}/{total}"
    else:
        progress = ""
    return (
        '<div class="batch-banner">'
        f"{verb} mail{progress}. The board will refresh automatically."
        "</div>"
    )


def _health_alerts_html(
    account_health: dict[str, dict[str, Any] | None],
    account_labels: dict[str, str] | None = None,
    *,
    config_failures: tuple[tuple[str, str], ...] = (),
    account_configs: dict[str, object] | None = None,
) -> str:
    """Return a red banner listing every failing account, or ``""`` if all OK."""
    parts: list[str] = []

    # Config-load failures (no DB connection needed).
    for account_id, error in config_failures:
        parts.append(
            '<div class="health-alert-banner" role="alert">\n'
            f"  &#9888; <strong>Account config error &mdash; account"
            f" '{html.escape(account_id)}' could not be loaded and"
            f" is disabled:</strong> {html.escape(error)}\n"
            "</div>"
        )

    # DB-backed health failures.
    failures: list[tuple[str, str, str, bool]] = []
    for account_id, h in account_health.items():
        if h is not None and h.get("status") == "failed":
            label = (account_labels or {}).get(account_id, account_id)
            error = h.get("error", "Unknown error")
            cfg = (account_configs or {}).get(account_id)
            needs_auth = (
                cfg is not None
                and getattr(cfg, "oauth2_provider", "") == MICROSOFT_PROVIDER
            )
            failures.append((account_id, label, error, needs_auth))

    if failures:
        items_lines: list[str] = []
        for account_id_key, label, error, needs_auth in failures:
            btn = (
                f' <button class="auth-btn"'
                f' data-account-id="{html.escape(account_id_key)}"'
                ' onclick="authorizeAccount(this)">Authorize / Reconnect</button>'
                if needs_auth
                else ""
            )
            items_lines.append(
                f"    <li><strong>{html.escape(label)}</strong>: "
                f"{html.escape(error)}{btn}</li>"
            )
        items = "\n".join(items_lines)
        parts.append(
            '<div class="health-alert-banner" role="alert" id="health-alerts">\n'
            "  &#9888; <strong>Account connection failure</strong>\n"
            "  <ul>\n"
            f"{items}\n"
            "  </ul>\n"
            "</div>"
        )

    return "\n".join(parts)


def _render_board_page_shell(
    *,
    columns_html: str,
    triage_running: bool,
    picker_html: str,
    account_qs: str,
    fetch_qs: str,
    batch_control_html: str,
    data_account_js: bool,
    health_alerts_html: str = "",
) -> str:
    """Shared HTML page shell + JS for both single-account and global boards.

    All varying parts are passed as keyword arguments so the two callers
    (:func:`_build_board_html` and :func:`_build_global_board_html`) share
    the ~150-line shell without duplication.
    """
    triage_control_html: str
    if triage_running:
        triage_control_html = (
            '<div class="triage-banner">'
            "Triage is currently running. The board will refresh automatically."
            "</div>"
        )
    else:
        triage_control_html = ""

    # Build the #board-config JSON payload so robotsix-board's board.js
    # can activate and expose its public API (robotsixBoardRefresh, etc.).
    # Auto-mail's own board-auto-mail.js overlay composes on top via
    # capture-phase interceptor and reads its app-specific config from
    # the same element.
    board_config = {
        "render_mode": "json_hydration",
        "columns": [
            [action, TRIAGE_ACTION_LABELS[action]] for action in TRIAGE_ACTION_ORDER
        ],
        "account_qs": account_qs,
        "fetch_qs": fetch_qs,
        "data_account_js": data_account_js,
        "batch_op_verbs": sorted(BATCH_OP_VERBS),
        "batch_op_verb_labels": BATCH_OP_VERB_LABELS,
    }

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<title>Mail Board</title>\n"
        '<link rel="stylesheet" href="/static/board.css">\n'
        '<link rel="stylesheet" href="/static/automail/board.css">\n'
        "</head>\n"
        "<body>\n"
        + (health_alerts_html or '<div id="health-alerts"></div>\n')
        + (
            '<button id="probe-health-btn"'
            ' onclick="probeHealth()">'
            "Recheck connections</button>\n"
        )
        + "<h1>Mail Board</h1>\n"
        f'<span id="triage-control">{triage_control_html}</span>\n'
        f'<span id="batch-control">{batch_control_html}</span>\n'
        f"{picker_html}\n"
        '<div class="board-wrapper">\n'
        '<div class="board">\n'
        f"{columns_html}"
        "\n</div>\n"
        "</div>\n"
        # Side-panel skeleton (auto-mail's iframe-based pattern, not
        # the library's #drawer).
        '<div class="side-panel" id="side-panel">\n'
        '<div class="panel-header">\n'
        '<span class="panel-title"></span>\n'
        '<button class="close-btn" onclick="closeDetail()">&times;</button>\n'
        "</div>\n"
        '<iframe src="" title="Mail detail"></iframe>\n'
        "</div>\n"
        # board.js configuration — enables the library's public API and
        # harmless internal handlers (no #board element → all no-ops).
        '<script id="board-config" type="application/json">'
        f"{json.dumps(board_config)}"
        "</script>\n"
        # probeHealth() on-demand connection recheck (referenced by the
        # inline onclick on #probe-health-btn).
        "<script>\n"
        "function probeHealth() {\n"
        "  fetch('/probe-health').then(function() {\n"
        "    window.location.reload();\n"
        "  });\n"
        "}\n"
        "</script>\n"
        '<script src="/static/board.js"></script>\n'
        # App-specific overlay — must load after board.js so the
        # capture-phase interceptor can override board.js's bubble
        # handler for card clicks.
        '<script src="/static/board-auto-mail.js"></script>\n'
        "</body>\n"
        "</html>"
    )


def _build_board_html(
    db_path: str,
    archive_root: str = DEFAULT_ARCHIVE_ROOT,
    *,
    accounts: MailAccountsConfig | None = None,
    current_account_id: str | None = None,
) -> str:
    """Build the full ``/board`` HTML document.

    Calls :func:`_build_board_content` and wraps the result in a minimal
    HTML5 page shell (Python f-strings, no Jinja2).  Raises ``Exception``
    when the database cannot be opened (the caller should catch it and
    return a 503).

    When *accounts* describes two or more accounts an account picker is
    rendered in the header and the resolved *current_account_id* is
    threaded into the JS-built GET URLs (detail iframe, content refresh)
    so deep-links and cookie-less clients route correctly.  In the
    single-account / legacy path (*accounts* ``None`` or fewer than two
    accounts) the served HTML is byte-for-byte unchanged apart from an
    empty picker slot.
    """
    content = _build_board_content(
        db_path,
        archive_root=archive_root,
        account_id=current_account_id or "main",
        config_failures=(),
    )

    # -- per-account health for picker badges ---------------------------
    # Lightweight read of each account's health watermark so the picker
    # can show [FAILED] badges on failing accounts.  Only done when
    # multiple accounts are configured.
    picker_health: dict[str, dict[str, Any] | None] = {}
    if accounts is not None and len(accounts.ids()) >= 2:
        from robotsix_auto_mail.db.queries import get_account_health as _get_h

        for a in accounts.accounts:
            try:
                with _with_db(a.config.db_path, skip_migrations=True) as c:
                    picker_health[a.account_id] = _get_h(c)
            except Exception:
                picker_health[a.account_id] = None

    # -- account picker + URL threading -----------------------------------
    # The picker only appears when more than one account is configured.
    # Switching accounts navigates to ``/board?account=<id>`` which the
    # already-landed ``_select_account`` resolves and persists via the
    # ``account`` cookie — every subsequent same-origin request (POST
    # forms, iframe, fetch) then routes to the chosen account.  POST form
    # bodies deliberately carry no account field: ``_select_account``
    # reads only the URL query or the cookie, so a hidden field would be
    # ignored; the cookie is the persistence mechanism.
    picker_html = ""
    account_qs = ""
    fetch_qs = ""
    multi_account = accounts is not None and len(accounts.ids()) >= 2
    if multi_account and accounts is not None:
        options_parts: list[str] = ['<option value="__all__">All mailboxes</option>']
        for account_id in accounts.ids():
            account = accounts.get(account_id)
            display = account.label if account.label else account.account_id
            # Per-account FAILED badge when the health watermark shows a failure.
            h = picker_health.get(account_id) if picker_health else None
            if h is not None and h.get("status") == "failed":
                display += " [FAILED]"
            sel = " selected" if account_id == current_account_id else ""
            options_parts.append(
                f'<option value="{html.escape(account_id)}"{sel}>'
                f"{html.escape(display)}</option>"
            )
        picker_html = (
            '<label class="account-picker-label">Mailbox:&nbsp;'
            '<select id="account-picker"'
            " onchange=\"window.location.href='/board?account='"
            '+encodeURIComponent(this.value)">'
            f"{''.join(options_parts)}"
            "</select></label>"
        )
    if multi_account and current_account_id:
        account_qs = "&account=" + quote(current_account_id, safe="")
        fetch_qs = "?account=" + quote(current_account_id, safe="")

    batch_control_html = _batch_banner_html(content["batch_op"])

    return _render_board_page_shell(
        columns_html=content["columns_html"],
        triage_running=content["triage_running"],
        picker_html=picker_html,
        account_qs=account_qs,
        fetch_qs=fetch_qs,
        batch_control_html=batch_control_html,
        data_account_js=False,
        health_alerts_html=content.get("health_alerts_html", ""),
    )


def _build_global_board_html(
    accounts: MailAccountsConfig,
    *,
    current_account_id: str = "__all__",
) -> str:
    """Build the full ``/board`` HTML document for the aggregate view.

    Renders every configured account's records in a single unified board,
    with a picker whose first option is ``All mailboxes`` (selected by
    default).  Card click handlers derive the owning account from
    ``data-account`` rather than a page-level ``account_qs`` so each
    per-card detail link and form routes to the correct single-account DB.

    Folder-triage is hidden in the aggregate view (it requires a single
    account's IMAP connection).
    """
    content = _build_global_board_content(accounts)

    # Per-account FAILED badge for the picker.
    acct_health = content.get("account_health", {})
    # Account picker — first option is the global view.
    options_parts: list[str] = [
        '<option value="__all__" selected>All mailboxes</option>'
    ]
    for account_id in accounts.ids():
        account = accounts.get(account_id)
        display = account.label if account.label else account.account_id
        h = acct_health.get(account_id)
        if h is not None and h.get("status") == "failed":
            display += " [FAILED]"
        options_parts.append(
            f'<option value="{html.escape(account_id)}">{html.escape(display)}</option>'
        )
    picker_html = (
        '<label class="account-picker-label">Mailbox:&nbsp;'
        '<select id="account-picker"'
        " onchange=\"window.location.href='/board?account='"
        '+encodeURIComponent(this.value)">'
        f"{''.join(options_parts)}"
        "</select></label>"
    )

    # Aggregate view always polls /board-content?account=__all__ and
    # has no page-level account_qs for the detail iframe (each card
    # carries its own data-account).
    fetch_qs = "?account=__all__"

    return _render_board_page_shell(
        columns_html=content["columns_html"],
        triage_running=content["triage_running"],
        picker_html=picker_html,
        account_qs="",  # page-level; cards carry their own
        fetch_qs=fetch_qs,
        # Combined progress of the fanned-out per-account batch workers.
        batch_control_html=_batch_banner_html(content.get("batch_op")),
        data_account_js=True,
        health_alerts_html=content.get("health_alerts_html", ""),
    )
