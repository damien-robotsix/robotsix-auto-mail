"""Human-readable triage rules file, maintained by a flash LLM.

Replaces the former per-sender JSON memory ledgers
(``triage_human_memory`` / ``archive_folder_memory``) with a single
Markdown file, ``triage_rules.md``, that a human can read and edit.

Whenever the user takes a triage action (board move, archive-to-folder,
save-draft, CLI ``triage-set``), a cheap ("flash") LLM is given the current
rules plus the action and the mail's **sender, subject, and body** and
rewrites the rules file only when a rule should change.  The triage agent and
the archive-subfolder proposal read this file into their prompts, so triage
reasons over the whole mail context guided by human-readable rules.

The file lives next to each account's SQLite datastore
(``<db-dir>/triage_rules.md``) unless ``MailConfig.triage_rules_path`` is set.
Updates are best-effort — a failure (missing key, network, LLM error) is
swallowed so a user action is never blocked — and serialised per path so
concurrent actions do not race the file.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pydantic

from robotsix_auto_mail.config import (
    MailConfig,
    resolve_llm_api_key,
    resolve_llm_provider_model,
)
from robotsix_auto_mail.core._llm_agent import _run_llm_agent
from robotsix_auto_mail.core.format import _effective_body_plain
from robotsix_auto_mail.db.models import MailRecord
from robotsix_auto_mail.triage.persistence import TriageError

_RULES_FILENAME = "triage_rules.md"

#: Mail body characters sent to the flash LLM (bounds token cost).
_BODY_LIMIT = 2000

#: Seed content for a brand-new rules file — a human-facing explanation the
#: LLM is told to preserve.
DEFAULT_RULES_HEADER = """# Triage rules

<!--
This file is maintained automatically from your board actions by a small
"flash" LLM, but it is meant to be read and edited by a human.  Each rule
describes, in plain language, how mail matching some sender / topic / content
should be triaged or archived.  Reword or delete any rule you disagree with —
the triage agent reads this file on every run.
-->

<!--
Example rules — uncomment and customise:

# - Archive newsletters from `newsletter@example.com` to `Newsletters`
# - Delete all mail from `no-reply@spammy-site.com`
# - Answer mail from `boss@mycompany.com` (needs personal reply)
# - Archive receipts and invoices to `Finance`
# - Archive CI notification emails from `github.com` to `Notifications`
# - Calendar invites from any sender → TO_CALENDAR
# - Mark `[email-verification]` subject mails as TO_ANSWER (time-sensitive)
-->
"""


class RulesMarkdown(pydantic.BaseModel):
    """Structured LLM output — the full updated triage-rules Markdown."""

    markdown: str


# Serialise concurrent read-modify-write of a rules file across worker threads.
_file_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    """Return a process-wide lock unique to *path*."""
    key = str(path)
    with _locks_guard:
        lock = _file_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _file_locks[key] = lock
        return lock


def resolve_rules_path(*, db_path: str, rules_path: str = "") -> Path | None:
    """Return the triage-rules file path, or ``None`` when there is no home.

    An explicit *rules_path* wins.  Otherwise the path is derived from
    *db_path* as ``<db-dir>/triage_rules.md``.  Returns ``None`` for an
    in-memory datastore (``":memory:"``) with no explicit path, since there is
    no natural on-disk location.
    """
    if rules_path:
        return Path(rules_path)
    if db_path == ":memory:":
        return None
    return Path(db_path).parent / _RULES_FILENAME


def load_rules(path: Path | None) -> str:
    """Return the rules-file text, or ``""`` when absent / unset."""
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def rules_text_for(config: MailConfig | None) -> str:
    """Return the triage-rules text for *config*'s account, or ``""``.

    Convenience for read-side callers (the archive-subfolder proposal and the
    triage agent) that have a :class:`MailConfig` in hand.
    """
    if config is None:
        return ""
    return load_rules(
        resolve_rules_path(db_path=config.db_path, rules_path=config.triage_rules_path)
    )


def _write_rules(path: Path, text: str) -> None:
    """Atomically write *text* to *path* (creating parent dirs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _build_rules_system_prompt() -> str:
    """System prompt instructing the flash LLM to maintain the rules file."""
    return (
        "You maintain a concise, human-readable set of email triage rules in "
        "Markdown.  You are given the current rules and a single triage action "
        "the user just took on one email — its sender, subject, and body.\n\n"
        "Triage actions:\n"
        "- INBOX: leave in the inbox (not triaged)\n"
        "- HUMAN_TRIAGE: a human must decide\n"
        "- PENDING_ACTION: waiting on someone or something\n"
        "- TO_ARCHIVE: archive it (optionally into a named folder)\n"
        "- TO_DELETE: delete it\n"
        "- TO_CALENDAR: it references a date/event\n"
        "- TO_ANSWER: it needs a reply\n"
        "- DRAFT_READY: a reply draft has been prepared\n\n"
        "Infer a GENERAL rule from this single action based on the WHOLE "
        "context of the mail (sender, sender domain, subject, and body) — not "
        "the subject alone.  Prefer generalising by sender, sender domain, or "
        "topic, and note the archive folder when one was chosen.\n\n"
        "Update the rules only when this action adds information: add a new "
        "rule, refine or merge an existing one, or leave the rules unchanged "
        "when they already cover this case or the action is a one-off with no "
        "generalisable pattern.  Keep the list short and non-redundant — do "
        "not accumulate near-duplicate rules.\n\n"
        "Return ONLY the complete updated Markdown document (the full file "
        "contents), preserving the existing header and any human-written "
        "comments."
    )


def _build_rules_user_message(
    *,
    action: str,
    sender: str,
    subject: str,
    body: str,
    subfolder: str,
    current_rules: str,
) -> str:
    """Build the user message describing the action and current rules."""
    folder_line = f"\nArchive folder chosen: {subfolder}" if subfolder else ""
    return (
        f"Current triage rules:\n{current_rules}\n\n"
        "--- User action ---\n"
        f"Action: {action}{folder_line}\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        f"Body:\n{body}\n"
    )


def update_rules_for_action(
    path: Path | None,
    *,
    action: str,
    sender: str,
    subject: str,
    body: str,
    subfolder: str = "",
    api_key: str | None = None,
    provider_model: str | None = None,
) -> None:
    """Best-effort: run the flash LLM to update *path* for a user action.

    A no-op when *path* is ``None`` or no LLM API key is resolvable.  Any
    failure is swallowed so a user action never fails because of rule
    maintenance.  Serialised per path so concurrent actions do not race.
    """
    if path is None:
        return
    resolved_key = resolve_llm_api_key(api_key, raise_on_missing=False)
    if not resolved_key:
        return
    with _lock_for(path):
        current = load_rules(path) or DEFAULT_RULES_HEADER
        try:
            result = _run_llm_agent(
                api_key=resolved_key,
                provider_model=resolve_llm_provider_model(provider_model),
                level=1,
                system_prompt=_build_rules_system_prompt(),
                output_model=RulesMarkdown,
                user_message=_build_rules_user_message(
                    action=action,
                    sender=sender,
                    subject=subject,
                    body=body[:_BODY_LIMIT],
                    subfolder=subfolder,
                    current_rules=current,
                ),
                label="triage rules update",
                what="triage rules update",
                exc_type=TriageError,
            )
            new_text = result.markdown.strip()
            if new_text and new_text != current.strip():
                _write_rules(path, new_text + "\n")
        except Exception:
            return


def record_user_action(
    record: MailRecord,
    action: str,
    *,
    config: MailConfig,
    subfolder: str = "",
    background: bool = True,
) -> None:
    """Record a user triage *action* by updating the triage-rules file.

    Resolves the per-account rules path from *config*, extracts the mail's
    sender / subject / body from *record*, and runs the flash-LLM rules
    update — in a background daemon thread when *background* (server request
    handlers), or inline otherwise (CLI).  Best-effort; never raises.
    """
    path = resolve_rules_path(
        db_path=config.db_path, rules_path=config.triage_rules_path
    )
    if path is None:
        return
    kwargs = {
        "action": action,
        "sender": record.sender,
        "subject": record.subject,
        "body": _effective_body_plain(record),
        "subfolder": subfolder,
        "api_key": config.llm_api_key or None,
        "provider_model": config.llm_provider_model or None,
    }
    if background:
        threading.Thread(
            target=update_rules_for_action,
            args=(path,),
            kwargs=kwargs,
            daemon=True,
        ).start()
    else:
        update_rules_for_action(
            path,
            action=action,
            sender=record.sender,
            subject=record.subject,
            body=_effective_body_plain(record),
            subfolder=subfolder,
            api_key=config.llm_api_key or None,
            provider_model=config.llm_provider_model or None,
        )
