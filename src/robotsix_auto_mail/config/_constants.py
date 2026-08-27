"""Canonical LLM application-name constants.

Central definition of the five LLM-powered application names so that
call sites never repeat raw string literals — a typo in an app name
silently defaults to tier level 1 in ``resolve_application_level()``,
so this module provides the single source of truth and a validation
guard.
"""

from __future__ import annotations

from typing import Final

APP_TRIAGE: Final = "triage"
APP_CLASSIFIER: Final = "classifier"
APP_DETECTOR: Final = "detector"
APP_DRAFT: Final = "draft"
