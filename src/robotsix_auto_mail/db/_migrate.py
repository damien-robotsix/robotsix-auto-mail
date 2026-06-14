"""Thin, reusable helpers for additive SQLite schema migrations.

This module encapsulates the "add-column-if-missing, additive,
idempotent" idiom that ``init_db`` previously hand-rolled once per
column.  An additive migration adds a new column to an existing table
when it is missing and is a no-op when the column already exists.

The helpers here are intentionally **backend-agnostic in shape**: they
rely only on ``conn.execute(...)`` + ``conn.commit()`` and on catching
the driver's "duplicate column" :class:`sqlite3.OperationalError`.  That
makes them promotable verbatim into a fleet-shared library consumed by
both raw-``sqlite3`` and SQLAlchemy callers.  For now the ``conn``
parameter is typed as :class:`sqlite3.Connection` because auto-mail only
ever passes a raw connection; the module deliberately does not import or
couple to SQLAlchemy/SQLModel.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence


def add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column_ddl: str,
) -> bool:
    """Add a column to *table* if it does not already exist.

    Executes ``ALTER TABLE {table} ADD COLUMN {column_ddl}`` inside a
    ``try``/``except sqlite3.OperationalError`` block.  On success the
    change is committed and ``True`` is returned.  When the column
    already exists the driver raises ``sqlite3.OperationalError`` which
    is caught; ``False`` is returned without raising.

    *table* and *column_ddl* are internal, code-controlled constants
    (never user input), so the f-string interpolation into the DDL is
    safe here.

    The helper is backend-agnostic in shape — it relies only on
    ``conn.execute(...)`` + ``conn.commit()`` and on catching the
    driver's "duplicate column" ``OperationalError`` — so it can later
    be promoted into a fleet-shared library used by raw-``sqlite3`` and
    SQLAlchemy callers alike.
    """
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_ddl}")
        conn.commit()
    except sqlite3.OperationalError:
        return False
    else:
        return True


def run_additive_migrations(
    conn: sqlite3.Connection,
    table: str,
    column_ddls: Sequence[str],
) -> None:
    """Apply each additive column migration in *column_ddls* to *table*.

    Iterates *column_ddls* in order, calling :func:`add_column_if_missing`
    for each.  This sequencer replaces a waterfall of near-identical
    single-column migration functions; it is idempotent because each
    individual step is.
    """
    for column_ddl in column_ddls:
        add_column_if_missing(conn, table, column_ddl)
