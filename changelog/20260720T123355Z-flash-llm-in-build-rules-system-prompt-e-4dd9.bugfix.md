Tell the triage-rules flash LLM which actions the automated triage agent
can actually assign (HUMAN_TRIAGE, TO_ARCHIVE, TO_DELETE, TO_ANSWER) so it
does not suggest rules using human-only actions like TO_CALENDAR.
Also fix the DEFAULT_RULES_HEADER example that incorrectly referenced TO_CALENDAR.
