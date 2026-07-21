"""Tests for the human-readable triage-rules file (``triage/rules.py``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.triage import rules as rules_mod
from robotsix_auto_mail.triage.rules import (
    RulesMarkdown,
    load_rules,
    record_user_action,
    resolve_rules_path,
    rules_text_for,
    update_rules_for_action,
)
from tests.conftest import _make_record


def _config(tmp_path: Path, **overrides: str) -> MailConfig:
    """Build a minimal MailConfig pointing its DB at *tmp_path*."""
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="pw",
        db_path=overrides.get("db_path", str(tmp_path / "mail.db")),
        llm_api_key=overrides.get("llm_api_key", ""),
        triage_rules_path=overrides.get("triage_rules_path", ""),
    )


# ---------------------------------------------------------------------------
# resolve_rules_path
# ---------------------------------------------------------------------------


def test_resolve_rules_path_explicit_wins(tmp_path: Path) -> None:
    explicit = str(tmp_path / "custom.md")
    assert resolve_rules_path(
        db_path=str(tmp_path / "mail.db"), rules_path=explicit
    ) == (Path(explicit))


def test_resolve_rules_path_derives_from_db_path(tmp_path: Path) -> None:
    db = tmp_path / "sub" / "mail.db"
    assert resolve_rules_path(db_path=str(db)) == tmp_path / "sub" / "triage_rules.md"


def test_resolve_rules_path_memory_is_none() -> None:
    assert resolve_rules_path(db_path=":memory:") is None


def test_resolve_rules_path_memory_with_explicit(tmp_path: Path) -> None:
    explicit = str(tmp_path / "r.md")
    assert resolve_rules_path(db_path=":memory:", rules_path=explicit) == Path(explicit)


# ---------------------------------------------------------------------------
# load_rules / rules_text_for
# ---------------------------------------------------------------------------


def test_load_rules_missing_returns_empty(tmp_path: Path) -> None:
    assert load_rules(tmp_path / "nope.md") == ""


def test_load_rules_none_returns_empty() -> None:
    assert load_rules(None) == ""


def test_load_rules_reads_content(tmp_path: Path) -> None:
    path = tmp_path / "triage_rules.md"
    path.write_text("# Rules\n- archive newsletters\n", encoding="utf-8")
    assert load_rules(path) == "# Rules\n- archive newsletters\n"


def test_rules_text_for_none_config() -> None:
    assert rules_text_for(None) == ""


def test_rules_text_for_reads_file(tmp_path: Path) -> None:
    path = tmp_path / "triage_rules.md"
    path.write_text("rule body", encoding="utf-8")
    config = _config(tmp_path, triage_rules_path=str(path))
    assert rules_text_for(config) == "rule body"


# ---------------------------------------------------------------------------
# update_rules_for_action
# ---------------------------------------------------------------------------


def test_update_rules_none_path_is_noop() -> None:
    # Should simply return without raising.
    update_rules_for_action(
        None, action="TO_ARCHIVE", sender="a@x.com", subject="s", body="b"
    )


def test_update_rules_no_api_key_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "triage_rules.md"
    called = False

    def _fake_agent(**_kwargs: object) -> RulesMarkdown:  # pragma: no cover
        nonlocal called
        called = True
        return RulesMarkdown(markdown="x")

    monkeypatch.setattr(rules_mod, "_run_llm_agent", _fake_agent)
    update_rules_for_action(
        path, action="TO_ARCHIVE", sender="a@x.com", subject="s", body="b"
    )
    assert not called
    assert not path.exists()


def test_update_rules_writes_new_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "triage_rules.md"
    captured: dict[str, object] = {}

    def _fake_agent(**kwargs: object) -> RulesMarkdown:
        captured.update(kwargs)
        return RulesMarkdown(markdown="# Triage rules\n\n- Archive mail from a@x.com\n")

    monkeypatch.setattr(rules_mod, "_run_llm_agent", _fake_agent)
    update_rules_for_action(
        path,
        action="TO_ARCHIVE",
        sender="a@x.com",
        subject="Weekly digest",
        body="lots of news",
        subfolder="Newsletters",
        api_key="sk-test",
    )
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Archive mail from a@x.com" in text
    # The action + mail context reach the LLM user message.
    user_message = str(captured["user_message"])
    assert "TO_ARCHIVE" in user_message
    assert "Weekly digest" in user_message
    assert "Newsletters" in user_message


def test_update_rules_unchanged_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "triage_rules.md"
    path.write_text("# Triage rules\n\n- Existing rule\n", encoding="utf-8")
    before = path.stat().st_mtime_ns

    def _fake_agent(**_kwargs: object) -> RulesMarkdown:
        # Return the same content (stripped-equal) → no rewrite.
        return RulesMarkdown(markdown="# Triage rules\n\n- Existing rule")

    monkeypatch.setattr(rules_mod, "_run_llm_agent", _fake_agent)
    update_rules_for_action(
        path, action="INBOX", sender="a@x.com", subject="s", body="b", api_key="sk-test"
    )
    assert path.read_text(encoding="utf-8") == "# Triage rules\n\n- Existing rule\n"
    assert path.stat().st_mtime_ns == before


def test_update_rules_swallows_llm_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "triage_rules.md"

    def _boom(**_kwargs: object) -> RulesMarkdown:
        raise RuntimeError("llm down")

    monkeypatch.setattr(rules_mod, "_run_llm_agent", _boom)
    # Must not raise; must not create the file.
    update_rules_for_action(
        path, action="TO_DELETE", sender="a@x.com", subject="s", body="b", api_key="k"
    )
    assert not path.exists()


# ---------------------------------------------------------------------------
# record_user_action
# ---------------------------------------------------------------------------


def test_record_user_action_inline_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        rules_mod,
        "_run_llm_agent",
        lambda **_k: RulesMarkdown(markdown="# Triage rules\n\n- Reply to boss\n"),
    )
    config = _config(tmp_path, llm_api_key="sk-test")
    record = _make_record(
        message_id="<m1@x.com>", sender="boss@x.com", subject="Q3 plan"
    )
    record_user_action(record, "TO_ANSWER", config=config, background=False)
    rules_file = tmp_path / "triage_rules.md"
    assert rules_file.exists()
    assert "Reply to boss" in rules_file.read_text(encoding="utf-8")


def test_record_user_action_memory_db_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def _fake(**_k: object) -> None:  # pragma: no cover
        nonlocal called
        called = True

    monkeypatch.setattr(rules_mod, "update_rules_for_action", _fake)
    config = MailConfig(
        imap_host="i",
        smtp_host="s",
        username="u@x.com",
        password="p",
        db_path=":memory:",
        llm_api_key="sk-test",
    )
    record = _make_record(message_id="<m@x.com>", sender="a@x.com", subject="s")
    record_user_action(record, "TO_ARCHIVE", config=config, background=False)
    assert not called
