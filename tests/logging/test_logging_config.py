from __future__ import annotations

import json
import logging
import os

import pytest
import structlog

from robotsix_auto_mail.logging import setup_logging

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset root logger state so each test starts from a clean slate."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)  # default before setup_logging
    # Clear LOG_FILE_DIR from env (no longer read by setup_logging, but kept
    # for safety in case other code reads it).
    monkeypatch.setenv("LOG_FILE_DIR", "")


# ---------------------------------------------------------------------------


def test_log_level_honoured() -> None:
    """A level=WARNING arg sets the root logger level to WARNING."""
    setup_logging(level="WARNING", log_file_dir="")

    assert logging.getLogger().level == logging.WARNING


def test_default_log_level_is_info() -> None:
    """With no ``level`` arg, the default level is INFO."""
    setup_logging(log_file_dir="")

    assert logging.getLogger().level == logging.INFO


def test_json_format_renders_parseable_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """log_format=json renders each event as a single parseable JSON line."""
    # Force basicConfig to attach a fresh handler bound to the captured stdout.
    logging.getLogger().handlers.clear()

    setup_logging(level="INFO", log_format="json", log_file_dir="")

    logger = structlog.get_logger("test.logging")
    logger.info("hello_event", foo="bar")

    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert lines, "expected a log line on stdout"
    payload = json.loads(lines[-1])
    assert payload["event"] == "hello_event"
    assert payload["foo"] == "bar"
    assert payload["level"] == "info"


# ---------------------------------------------------------------------------
# File handler tests
# ---------------------------------------------------------------------------


def test_file_handler_creates_log_file(
    tmp_path: str,
) -> None:
    """With log_file_dir set to a writable tmp_path, a date-stamped log
    file is created and receives log events."""
    setup_logging(level="INFO", log_file_dir=str(tmp_path))

    logger = structlog.get_logger("test.file")
    logger.info("file_log_test", extra="value")

    # The log file is date-stamped with today's date.
    import datetime

    today = datetime.date.today().isoformat()
    log_path = os.path.join(str(tmp_path), f"mail-{today}.log")
    assert os.path.isfile(log_path), f"expected {log_path} to exist"

    content = open(log_path).read()
    assert "file_log_test" in content


def test_file_handler_always_debug(
    tmp_path: str,
) -> None:
    """When level=WARNING, the file handler still receives DEBUG events."""
    setup_logging(level="WARNING", log_file_dir=str(tmp_path))

    logger = structlog.get_logger("test.debug")
    logger.debug("debug_for_file_only")

    import datetime

    today = datetime.date.today().isoformat()
    log_path = os.path.join(str(tmp_path), f"mail-{today}.log")
    assert os.path.isfile(log_path)

    content = open(log_path).read()
    assert "debug_for_file_only" in content


def test_file_handler_uncreatable_path_does_not_crash() -> None:
    """When log_file_dir points to an uncreatable path, setup_logging
    does not raise and stdout logging still works."""
    # /dev/null is a file, not a directory — creating a subdirectory
    # inside it must fail.
    setup_logging(level="INFO", log_file_dir="/dev/null/.mail_log")

    # The root logger should still have a StreamHandler for stdout.
    root = logging.getLogger()
    stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
    assert stream_handlers, "expected a stdout StreamHandler to exist"


def test_empty_log_file_dir_disables_file_logging() -> None:
    """Empty log_file_dir (or whitespace-only) means no file handler."""
    setup_logging(level="INFO", log_file_dir="   ")

    root = logging.getLogger()
    file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    assert not file_handlers, "expected no FileHandler for empty log_file_dir"
