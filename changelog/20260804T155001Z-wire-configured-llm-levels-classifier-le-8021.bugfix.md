Wire `detector_level` and `draft_level` config fields into their
respective LLM calls so they no longer silently fall back to tier 1.
Both fields were already defined and schema-validated but the resolved
level was discarded at the call site.  `classifier_level` was already
correctly wired in a prior change.
