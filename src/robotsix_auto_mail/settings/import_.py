"""One-time import: seed the internal settings store from central-deploy."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

#: Environment variable that holds the central-deploy config-export URL.
#: Injected at container boot time by central-deploy; absent in local dev.
_CENTRAL_DEPLOY_EXPORT_URL_ENV: str = "CENTRAL_DEPLOY_EXPORT_URL"


def _fetch_export(url: str) -> dict[str, Any]:
    """Call the central-deploy export endpoint and return the parsed JSON.

    Uses only stdlib (:mod:`urllib.request`) so the import works without
    any optional dependency.  The export endpoint is expected to return a
    JSON object whose keys match ``MailConfig`` field names.

    Raises:
        OSError: On network / HTTP-level failures.
        json.JSONDecodeError: When the response body is not valid JSON.
        ValueError: When the response is not a JSON object.
    """
    import urllib.request

    req = urllib.request.Request(  # noqa: S310 — HTTPS expected
        url,
        method="GET",
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — HTTPS expected
        body = resp.read().decode("utf-8")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError(
            f"central-deploy export returned a {type(data).__name__}, "
            f"expected a JSON object"
        )
    # The export endpoint may return a nested structure; accept either a
    # flat {field: value} dict or {"config": {field: value}}.
    if "config" in data and isinstance(data["config"], dict):
        return data["config"]
    return data


def import_from_central_deploy(
    store: object,  # SettingsStore
    conn: object,  # sqlite3.Connection
) -> bool:
    """Seed *store* from central-deploy's config export endpoint.

    Only runs when the store is **empty** (``store.is_empty(conn)``
    returns ``True``) — subsequent calls are a no-op so restarting
    the service never overwrites locally-edited settings.

    The export URL is read from the ``CENTRAL_DEPLOY_EXPORT_URL``
    environment variable.  When the variable is absent the import is
    skipped silently (the service falls back to ``config.json`` for
    bootstrap settings).

    Returns ``True`` when the import completed and seeded at least
    one setting, ``False`` otherwise.
    """
    from robotsix_auto_mail.settings.store import SettingsStore

    if not isinstance(store, SettingsStore):
        raise TypeError(
            f"import_from_central_deploy expects a SettingsStore, "
            f"got {type(store).__name__}"
        )

    if not store.is_empty(conn):
        logger.debug("Settings store already populated — skipping import.")
        return False

    export_url = os.environ.get(_CENTRAL_DEPLOY_EXPORT_URL_ENV)
    if not export_url:
        logger.info(
            "%s not set — skipping central-deploy import. "
            "The service will use config.json for bootstrap settings.",
            _CENTRAL_DEPLOY_EXPORT_URL_ENV,
        )
        return False

    logger.info("Importing settings from central-deploy: %s", export_url)
    try:
        settings = _fetch_export(export_url)
    except Exception as exc:
        logger.warning(
            "Failed to import settings from central-deploy (%s): %s",
            export_url,
            exc,
        )
        return False

    # Validate and seed: only store keys that exist on MailConfig.
    from robotsix_auto_mail.config.model import MailConfig

    valid_keys = set(MailConfig.model_fields)
    filtered = {k: str(v) for k, v in settings.items() if k in valid_keys}
    if not filtered:
        logger.warning("central-deploy export contained no recognised settings.")
        return False

    from robotsix_auto_mail.db import set_component_settings as _set

    _set(conn, filtered)  # type: ignore[arg-type]
    logger.info("Imported %d settings from central-deploy.", len(filtered))
    return True
