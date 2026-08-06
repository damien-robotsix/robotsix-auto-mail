"""Config version history — the store behind ``GET /config/versions``.

Every successful write records a snapshot next to ``config.json`` so an
operator can see what changed and roll back.  **Secret values are never
stored**: a snapshot keeps only non-secret fields, and the entry records
that a secret key changed, not its content (robotsix-standards
``config-ownership.md``).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: How many versions to retain.  The standard asks for at least 20.
RETENTION = 20

#: File name, kept beside the config file it describes.
VERSIONS_FILENAME = "config_versions.json"


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat()


class ConfigVersionStore:
    """Append-only version log for one config file."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        """The JSON file backing this store."""
        return self._path

    # -- read ---------------------------------------------------------------

    def _read(self) -> list[dict[str, Any]]:
        """Return the stored entries, newest first (empty when unreadable)."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (
            OSError,
            json.JSONDecodeError,
        ):
            # A corrupt history must never block reading or writing config.
            logger.warning("Unreadable config version history at %s", self._path)
            return []
        entries = raw.get("entries") if isinstance(raw, dict) else None
        return entries if isinstance(entries, list) else []

    def current_version(self) -> int:
        """The highest recorded version, or ``0`` when nothing is recorded."""
        entries = self._read()
        return int(entries[0].get("version", 0)) if entries else 0

    def entries(self) -> list[dict[str, Any]]:
        """Version metadata, newest first, without the stored payloads."""
        return [
            {
                "version": entry.get("version"),
                "timestamp": entry.get("timestamp"),
                "changed_keys": entry.get("changed_keys", []),
            }
            for entry in self._read()
        ]

    def snapshot(self, version: int) -> dict[str, Any] | None:
        """The secret-free config recorded for *version*, or ``None``."""
        for entry in self._read():
            if entry.get("version") == version:
                config = entry.get("config")
                return config if isinstance(config, dict) else None
        return None

    # -- write --------------------------------------------------------------

    def record(self, config: dict[str, Any], changed_keys: list[str]) -> int:
        """Append a version for *config* and return its number.

        *config* must already have its secret values stripped — this method
        stores whatever it is given.
        """
        entries = self._read()
        version = (int(entries[0].get("version", 0)) if entries else 0) + 1
        entries.insert(
            0,
            {
                "version": version,
                "timestamp": _now(),
                "changed_keys": changed_keys,
                "config": config,
            },
        )
        self._write(entries[:RETENTION])
        return version

    def _write(self, entries: list[dict[str, Any]]) -> None:
        payload = json.dumps({"entries": entries}, indent=2, sort_keys=True)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Written atomically so a crash mid-write cannot truncate the history
        # (the lifecycle_state.yaml truncation outage, twice over).
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)
