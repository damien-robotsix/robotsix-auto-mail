"""Shared test fixtures for server tests."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator

import pytest

from robotsix_auto_mail.config import MailAccountsConfig
from tests.server.conftest_helpers import (
    _two_account_setup,
    _two_account_setup_with_labels,
    _two_account_setup_with_triage,
)


@pytest.fixture
def db_accounts() -> Generator[tuple[str, str, MailAccountsConfig]]:
    """Yield (db_a, db_b, accounts) with two triage-seeded DBs; cleanup after."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup_with_triage(db_a, db_b)
        yield db_a, db_b, accounts
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


@pytest.fixture
def db_accounts_no_triage() -> Generator[tuple[str, str, MailAccountsConfig]]:
    """Yield (db_a, db_b, accounts) with two DBs (no triage seeding); cleanup after."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup(db_a, db_b)
        yield db_a, db_b, accounts
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


@pytest.fixture
def db_accounts_no_triage_b() -> Generator[tuple[str, str, MailAccountsConfig]]:
    """Yield (db_a, db_b, accounts), no triage seeding; default account is "B"."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup(db_a, db_b, default_account_id="B")
        yield db_a, db_b, accounts
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


@pytest.fixture
def db_accounts_with_labels_no_triage() -> Generator[
    tuple[str, str, MailAccountsConfig]
]:
    """Yield (db_a, db_b, accounts) with a non-None label on account A; cleanup after."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup_with_labels(db_a, db_b)
        yield db_a, db_b, accounts
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


@pytest.fixture
def single_db() -> Generator[str]:
    """Yield a single temp DB path; cleanup after."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        yield db_path
    finally:
        os.unlink(db_path)
