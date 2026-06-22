from __future__ import annotations

import datetime
import json
import logging
import os

import pytest

from robotsix_auto_mail.logging import setup_logging

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_robotsix_auto_mail_logger() -> None:
    """Reset the ``robotsix_auto_mail`` logger state so each test starts clean."""
    target = logging.getLogger("robotsix_auto_mail")
    target.handlers.clear()
    target.setLevel(logging.NOTSET)
    target.propagate = True
    # Remove llmio's idempotency marker so setup_logging isn't considered
    # already-configured.
    for name in ["robotsix_auto_mail", "robotsix_llmio"]:
        for h in logging.getLogger(name).handlers[:]:
            logging.getLogger(name).removeHandler(h)
            if hasattr(h, "close"):
                h.close()


@pytest.fixture(autouse=True)
def _reset_logging() -> None:
    """Reset logging state so each test starts from a clean slate."""
    _clear_robotsix_auto_mail_logger()
    logging.getLogger("robotsix_llmio").handlers.clear()


# ---------------------------------------------------------------------------


def test_log_level_honoured() -> None:
    """A level=WARNING arg sets the logger level to WARNING."""
    setup_logging(level="WARNING", log_file_dir="")

    assert logging.getLogger("robotsix_auto_mail").level == logging.WARNING


def test_default_log_level_is_info() -> None:
    """With no ``level`` arg, the default level is INFO."""
    setup_logging(log_file_dir="")

    assert logging.getLogger("robotsix_auto_mail").level == logging.INFO


def test_json_format_renders_parseable_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """log_format=json renders each event as a single parseable JSON line."""
    setup_logging(level="INFO", log_format="json", log_file_dir="")

    logger = logging.getLogger("robotsix_auto_mail")
    logger.info("hello_event foo=bar")

    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert lines, "expected a log line on stdout"
    payload = json.loads(lines[-1])
    assert payload["message"] == "hello_event foo=bar"
    assert payload["level"] == "INFO"
    assert "timestamp" in payload
    assert payload["logger"] == "robotsix_auto_mail"
    assert "trace_id" in payload


# ---------------------------------------------------------------------------
# Trace-id injection tests
# ---------------------------------------------------------------------------


def test_trace_id_no_span_renders_dash(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no active recording span, JSON output carries trace_id == \"-\"."""
    monkeypatch.setattr("robotsix_llmio.logging.get_recording_span", lambda: None)

    setup_logging(level="INFO", log_format="json", log_file_dir="")

    logger = logging.getLogger("robotsix_auto_mail")
    logger.info("no_span_event")

    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    payload = json.loads(lines[-1])
    assert payload["trace_id"] == "-"


def test_trace_id_active_span_renders_hex(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An active recording span stamps the 32-hex-char trace id."""
    trace_int = 0x0123456789ABCDEF0123456789ABCDEF

    class _Ctx:
        trace_id = trace_int

    class _Span:
        def get_span_context(self) -> "_Ctx":
            return _Ctx()

    monkeypatch.setattr("robotsix_llmio.logging.get_recording_span", lambda: _Span())

    setup_logging(level="INFO", log_format="json", log_file_dir="")

    logger = logging.getLogger("robotsix_auto_mail")
    logger.info("span_event")

    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    payload = json.loads(lines[-1])
    assert payload["trace_id"] == format(trace_int, "032x")
    assert len(payload["trace_id"]) == 32


def test_trace_id_present_in_console_format(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Console renderer also carries the trace_id key."""
    monkeypatch.setattr("robotsix_llmio.logging.get_recording_span", lambda: None)

    setup_logging(level="INFO", log_format="console", log_file_dir="")

    logger = logging.getLogger("robotsix_auto_mail")
    logger.info("console_event")

    out = capsys.readouterr().out
    assert "[-]" in out


# ---------------------------------------------------------------------------
# File handler tests
# ---------------------------------------------------------------------------


def test_file_handler_creates_log_file(
    tmp_path: str,
) -> None:
    """With log_file_dir set to a writable tmp_path, a date-stamped log
    file is created and receives log events."""
    setup_logging(level="INFO", log_file_dir=str(tmp_path))

    logger = logging.getLogger("robotsix_auto_mail")
    logger.info("file_log_test extra=value")

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

    logger = logging.getLogger("robotsix_auto_mail")
    logger.debug("debug_for_file_only")

    today = datetime.date.today().isoformat()
    log_path = os.path.join(str(tmp_path), f"mail-{today}.log")
    assert os.path.isfile(log_path)

    content = open(log_path).read()
    assert "debug_for_file_only" in content


def test_file_handler_uncreatable_path_does_not_crash() -> None:
    """When log_file_dir points to an uncreatable path, setup_logging
    does not raise and stdout logging still works."""
    setup_logging(level="INFO", log_file_dir="/dev/null/.mail_log")

    target = logging.getLogger("robotsix_auto_mail")
    stream_handlers = [
        h for h in target.handlers if isinstance(h, logging.StreamHandler)
    ]
    assert stream_handlers, "expected a stdout StreamHandler to exist"


def test_empty_log_file_dir_disables_file_logging() -> None:
    """Empty log_file_dir (or whitespace-only) means no file handler."""
    setup_logging(level="INFO", log_file_dir="   ")

    target = logging.getLogger("robotsix_auto_mail")
    file_handlers = [h for h in target.handlers if isinstance(h, logging.FileHandler)]
    assert not file_handlers, "expected no FileHandler for empty log_file_dir"
