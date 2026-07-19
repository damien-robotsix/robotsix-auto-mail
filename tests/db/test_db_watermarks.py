"""Tests for get_watermark and set_watermark."""

from __future__ import annotations

from robotsix_auto_mail.db import get_watermark, init_db, set_watermark

# ---------------------------------------------------------------------------
# Watermarks
# ---------------------------------------------------------------------------


def test_get_watermark_nonexistent_returns_none() -> None:
    """Never-set watermark returns None."""
    conn = init_db(":memory:")
    try:
        result = get_watermark(conn, "nonexistent")
        assert result is None
    finally:
        conn.close()


def test_set_and_get_watermark() -> None:
    """set_watermark followed by get_watermark returns the value."""
    conn = init_db(":memory:")
    try:
        set_watermark(conn, "last_uid", "42")
        assert get_watermark(conn, "last_uid") == "42"
    finally:
        conn.close()


def test_set_watermark_upserts() -> None:
    """Setting the same key twice updates the value (no duplicate rows)."""
    conn = init_db(":memory:")
    try:
        set_watermark(conn, "last_uid", "42")
        set_watermark(conn, "last_uid", "99")
        assert get_watermark(conn, "last_uid") == "99"

        # Only one row should exist for the key.
        cur = conn.execute(
            "SELECT COUNT(*) FROM watermark WHERE key = ?", ("last_uid",)
        )
        assert cur.fetchone()[0] == 1
    finally:
        conn.close()


def test_watermark_multiple_keys() -> None:
    """Different keys are independent."""
    conn = init_db(":memory:")
    try:
        set_watermark(conn, "last_uid", "10")
        set_watermark(conn, "other_key", "hello")
        assert get_watermark(conn, "last_uid") == "10"
        assert get_watermark(conn, "other_key") == "hello"
    finally:
        conn.close()


def test_watermark_across_connections() -> None:
    """Watermark persists across connections (using a temp file)."""
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)  # close the file descriptor; sqlite3 will open its own
    try:
        conn1 = init_db(path)
        set_watermark(conn1, "uid", "77")
        conn1.close()

        conn2 = init_db(path)
        assert get_watermark(conn2, "uid") == "77"
        conn2.close()
    finally:
        os.unlink(path)
