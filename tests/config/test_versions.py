"""Unit tests for ``ConfigVersionStore``.

Covers atomic writes, corrupt-JSON recovery, version numbering,
retention trimming, snapshot retrieval, and metadata extraction.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from robotsix_auto_mail.config.versions import RETENTION, ConfigVersionStore, _now

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    version: int,
    *,
    config: dict | None = None,
    changed_keys: list[str] | None = None,
    timestamp: str | None = None,
) -> dict:
    """Return a minimal valid history entry."""
    return {
        "version": version,
        "timestamp": timestamp if timestamp is not None else _now(),
        "changed_keys": changed_keys if changed_keys is not None else [],
        "config": config if config is not None else {"key": f"val-{version}"},
    }


def _write_history(path: Path, entries: list[dict]) -> None:
    """Write a ``config_versions.json``-shaped file at *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": entries}), encoding="utf-8")


# ---------------------------------------------------------------------------
# _read()
# ---------------------------------------------------------------------------


class TestRead:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        store = ConfigVersionStore(tmp_path / "nonexistent.json")
        assert store._read() == []

    def test_valid_json_returns_entries(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        _write_history(p, [_make_entry(1), _make_entry(2)])
        store = ConfigVersionStore(p)
        assert len(store._read()) == 2
        # _read() returns whatever is on disk; ordering is preserved as-stored.
        versions = [e["version"] for e in store._read()]
        assert 1 in versions
        assert 2 in versions

    def test_corrupt_json_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("not valid json {{{", encoding="utf-8")
        store = ConfigVersionStore(p)
        assert store._read() == []

    def test_corrupt_json_logs_warning(self, tmp_path: Path, caplog) -> None:
        p = tmp_path / "history.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("not valid json {{{", encoding="utf-8")
        store = ConfigVersionStore(p)
        with caplog.at_level(logging.WARNING):
            store._read()
        assert "Unreadable config version history" in caplog.text

    def test_non_dict_json_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("[1, 2, 3]", encoding="utf-8")
        store = ConfigVersionStore(p)
        assert store._read() == []

    def test_no_entries_key_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"other": "value"}', encoding="utf-8")
        store = ConfigVersionStore(p)
        assert store._read() == []

    def test_entries_not_a_list_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"entries": "not-a-list"}', encoding="utf-8")
        store = ConfigVersionStore(p)
        assert store._read() == []

    def test_oserror_returns_empty(self, tmp_path: Path) -> None:
        """A permission error or similar OSError returns empty list."""
        p = tmp_path / "history.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"entries": []}', encoding="utf-8")
        # Make the file unreadable
        p.chmod(0o000)
        try:
            store = ConfigVersionStore(p)
            assert store._read() == []
        finally:
            p.chmod(0o644)


# ---------------------------------------------------------------------------
# current_version()
# ---------------------------------------------------------------------------


class TestCurrentVersion:
    def test_empty_store_returns_zero(self, tmp_path: Path) -> None:
        store = ConfigVersionStore(tmp_path / "nonexistent.json")
        assert store.current_version() == 0

    def test_one_entry_returns_correct_version(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        _write_history(p, [_make_entry(5)])
        store = ConfigVersionStore(p)
        assert store.current_version() == 5


# ---------------------------------------------------------------------------
# entries()
# ---------------------------------------------------------------------------


class TestEntries:
    def test_returns_metadata_without_config(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        _write_history(p, [_make_entry(1, config={"secret": "x"})])
        store = ConfigVersionStore(p)
        result = store.entries()
        assert len(result) == 1
        entry = result[0]
        assert entry["version"] == 1
        assert "timestamp" in entry
        assert entry["changed_keys"] == []
        assert "config" not in entry  # payload stripped

    def test_empty_store_returns_empty_list(self, tmp_path: Path) -> None:
        store = ConfigVersionStore(tmp_path / "nonexistent.json")
        assert store.entries() == []


# ---------------------------------------------------------------------------
# snapshot()
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_existing_version_returns_config(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        _write_history(p, [_make_entry(1, config={"a": "b"})])
        store = ConfigVersionStore(p)
        assert store.snapshot(1) == {"a": "b"}

    def test_nonexistent_version_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        _write_history(p, [_make_entry(1)])
        store = ConfigVersionStore(p)
        assert store.snapshot(99) is None

    def test_missing_config_key_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        entry = _make_entry(1)
        del entry["config"]
        _write_history(p, [entry])
        store = ConfigVersionStore(p)
        assert store.snapshot(1) is None

    def test_non_dict_config_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        entry = _make_entry(1)
        entry["config"] = "not-a-dict"  # type: ignore[assignment]
        _write_history(p, [entry])
        store = ConfigVersionStore(p)
        assert store.snapshot(1) is None


# ---------------------------------------------------------------------------
# record()
# ---------------------------------------------------------------------------


class TestRecord:
    def test_first_record_gets_version_1(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        store = ConfigVersionStore(p)
        version = store.record({"key": "val"}, ["key"])
        assert version == 1

    def test_subsequent_record_increments(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        store = ConfigVersionStore(p)
        store.record({"a": 1}, ["a"])
        version = store.record({"a": 2}, ["a"])
        assert version == 2

    def test_version_persists_across_store_instances(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        store1 = ConfigVersionStore(p)
        store1.record({"a": 1}, ["a"])
        store2 = ConfigVersionStore(p)
        version = store2.record({"a": 2}, ["a"])
        assert version == 2

    def test_changed_keys_preserved(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        store = ConfigVersionStore(p)
        store.record({}, ["imap_host", "smtp_port"])
        entries = store._read()
        assert entries[0]["changed_keys"] == ["imap_host", "smtp_port"]

    def test_exceeded_retention_drops_oldest(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        store = ConfigVersionStore(p)
        # Write RETENTION + 2 entries
        for i in range(1, RETENTION + 3):
            store.record({"i": i}, [f"key-{i}"])
        entries = store._read()
        assert len(entries) == RETENTION
        # The oldest (version 1 and 2) should be dropped;
        # versions 3..RETENTION+2 remain
        versions = [e["version"] for e in entries]
        assert min(versions) == 3
        assert max(versions) == RETENTION + 2

    def test_entries_stored_newest_first(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        store = ConfigVersionStore(p)
        store.record({"a": 1}, ["a"])
        store.record({"b": 2}, ["b"])
        entries = store._read()
        versions = [e["version"] for e in entries]
        assert versions == [2, 1]

    def test_record_writes_to_disk(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        store = ConfigVersionStore(p)
        store.record({"a": 1}, ["a"])
        assert p.exists()
        raw = json.loads(p.read_text())
        assert len(raw["entries"]) == 1
        assert raw["entries"][0]["version"] == 1


# ---------------------------------------------------------------------------
# _write()
# ---------------------------------------------------------------------------


class TestWrite:
    def test_no_tmp_file_left_behind(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        store = ConfigVersionStore(p)
        store.record({"a": 1}, ["a"])
        # The .tmp file must be gone after a successful write
        siblings = list(p.parent.iterdir())
        sibling_names = [f.name for f in siblings]
        assert "history.tmp" not in sibling_names
        # Also confirm the real file exists
        assert "history.json" in sibling_names

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        p = tmp_path / "subdir" / "nested" / "history.json"
        store = ConfigVersionStore(p)
        store.record({"a": 1}, ["a"])
        assert p.exists()
        assert p.parent.exists()

    def test_write_then_read_roundtrip(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        store = ConfigVersionStore(p)
        store.record({"x": "y"}, ["x"])
        # A fresh instance reading the same file
        store2 = ConfigVersionStore(p)
        assert store2.current_version() == 1
        assert store2.snapshot(1) == {"x": "y"}

    def test_oserror_during_write_does_not_corrupt_existing(
        self, tmp_path: Path
    ) -> None:
        """If a write fails, existing history file must be untouched."""
        p = tmp_path / "history.json"
        # Write a valid entry first
        store = ConfigVersionStore(p)
        store.record({"initial": True}, ["initial"])
        initial_content = p.read_text()

        # Make the parent directory read-only so the next write fails.
        # mkdir(parents=True, exist_ok=True) succeeds because the dir already
        # exists, but write_text will raise PermissionError (an OSError).
        p.parent.chmod(0o500)

        store2 = ConfigVersionStore(p)
        try:
            with pytest.raises(PermissionError):
                store2.record({"b": 2}, ["b"])
        finally:
            p.parent.chmod(0o700)

        # The original file must still be intact
        assert p.read_text() == initial_content

    def test_chmod_sets_0600(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        store = ConfigVersionStore(p)
        store.record({"a": 1}, ["a"])
        stat = p.stat()
        # 0o600 in octal: owner rw-, group ---, others ---
        assert stat.st_mode & 0o777 == 0o600


# ---------------------------------------------------------------------------
# Integration-style: record + snapshot + entries
# ---------------------------------------------------------------------------


class TestRecordSnapshotEntriesIntegration:
    def test_full_cycle(self, tmp_path: Path) -> None:
        p = tmp_path / "history.json"
        store = ConfigVersionStore(p)

        # Record two versions
        v1 = store.record({"host": "a"}, ["host"])
        v2 = store.record({"host": "b"}, ["host"])

        assert v1 == 1
        assert v2 == 2

        # Snapshot
        assert store.snapshot(1) == {"host": "a"}
        assert store.snapshot(2) == {"host": "b"}

        # Entries — no config payloads
        entry_list = store.entries()
        assert len(entry_list) == 2
        assert "config" not in entry_list[0]
        assert "config" not in entry_list[1]
        assert entry_list[0]["version"] == 2
        assert entry_list[1]["version"] == 1

        # current_version
        assert store.current_version() == 2
