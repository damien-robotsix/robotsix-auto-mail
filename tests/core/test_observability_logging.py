from __future__ import annotations

import json
import logging

import pytest

from robotsix_auto_mail._observability import setup_logging

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
    setup_logging(level="WARNING")

    assert logging.getLogger("robotsix_auto_mail").level == logging.WARNING


def test_default_log_level_is_info() -> None:
    """With no ``level`` arg, the default level is INFO."""
    setup_logging()

    assert logging.getLogger("robotsix_auto_mail").level == logging.INFO


def test_json_format_renders_parseable_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """log_format=json renders each event as a single parseable JSON line."""
    setup_logging(level="INFO", log_format="json")

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

    setup_logging(level="INFO", log_format="json")

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

    setup_logging(level="INFO", log_format="json")

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

    setup_logging(level="INFO", log_format="console")

    logger = logging.getLogger("robotsix_auto_mail")
    logger.info("console_event")

    out = capsys.readouterr().out
    assert "[-]" in out
