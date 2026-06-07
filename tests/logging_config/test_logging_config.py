from __future__ import annotations

import json
import logging

import pytest
import structlog

from robotsix_auto_mail.logging_config import setup_logging


def test_log_level_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """LOG_LEVEL=WARNING sets the root logger level to WARNING."""
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.delenv("LOG_FORMAT", raising=False)

    setup_logging()

    assert logging.getLogger().level == logging.WARNING


def test_default_log_level_is_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no LOG_LEVEL set, the default level is INFO."""
    monkeypatch.delenv("LOG_LEVEL", raising=False)
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
