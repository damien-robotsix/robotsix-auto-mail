from __future__ import annotations

import json
import logging
import os

import pytest
import structlog

from robotsix_auto_mail.logging_config import setup_logging

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset root logger state so each test starts from a clean slate."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)  # default before setup_logging
    monkeypatch.setenv("LOG_FILE_DIR", "")


# ---------------------------------------------------------------------------


def test_log_level_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """LOG_LEVEL=WARNING sets the root logger level to WARNING."""
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("LOG_FILE_DIR", "")  # disable file logging
    monkeypatch.delenv("LOG_FORMAT", raising=False)

    setup_logging()

    assert logging.getLogger().level == logging.WARNING


def test_default_log_level_is_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no LOG_LEVEL set, the default level is INFO."""
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.setenv("LOG_FILE_DIR", "")  # disable file logging
    monkeypatch.delenv("LOG_FORMAT", raising=False)

    setup_logging()

    assert logging.getLogger().level == logging.INFO


def test_json_format_renders_parseable_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """LOG_FORMAT=json renders each event as a single parseable JSON line."""
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("LOG_FILE_DIR", "")  # disable file logging
    # Force basicConfig to attach a fresh handler bound to the captured stdout.
    logging.getLogger().handlers.clear()

    setup_logging()

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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: str,
) -> None:
    """With LOG_FILE_DIR set to a writable tmp_path, a date-stamped log
    file is created and receives log events."""
    monkeypatch.setenv("LOG_FILE_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.delenv("LOG_FORMAT", raising=False)

    setup_logging()

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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: str,
) -> None:
    """When LOG_LEVEL=WARNING, the file handler still receives DEBUG events."""
    monkeypatch.setenv("LOG_FILE_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.delenv("LOG_FORMAT", raising=False)

    setup_logging()

    logger = structlog.get_logger("test.debug")
    logger.debug("debug_for_file_only")

    import datetime

    today = datetime.date.today().isoformat()
    log_path = os.path.join(str(tmp_path), f"mail-{today}.log")
    assert os.path.isfile(log_path)

    content = open(log_path).read()
    assert "debug_for_file_only" in content


def test_file_handler_uncreatable_path_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When LOG_FILE_DIR points to an uncreatable path, setup_logging
    does not raise and stdout logging still works."""
    # /dev/null is a file, not a directory — creating a subdirectory
    # inside it must fail.
    monkeypatch.setenv("LOG_FILE_DIR", "/dev/null/.mail_log")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.delenv("LOG_FORMAT", raising=False)

    setup_logging()  # must not raise

    # The root logger should still have a StreamHandler for stdout.
    root = logging.getLogger()
    stream_handlers = [
        h for h in root.handlers if isinstance(h, logging.StreamHandler)
    ]
    assert stream_handlers, "expected a stdout StreamHandler to exist"


def test_empty_log_file_dir_disables_file_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty LOG_FILE_DIR (or whitespace-only) means no file handler."""
    monkeypatch.setenv("LOG_FILE_DIR", "   ")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.delenv("LOG_FORMAT", raising=False)

    setup_logging()

    root = logging.getLogger()
    file_handlers = [
        h for h in root.handlers if isinstance(h, logging.FileHandler)
    ]
    assert not file_handlers, "expected no FileHandler for empty LOG_FILE_DIR"
