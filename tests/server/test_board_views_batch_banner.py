"""Unit tests for ``_batch_banner_html`` from
``robotsix_auto_mail.server.views.board``.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# _batch_banner_html
# ---------------------------------------------------------------------------


def test_batch_banner_html_none() -> None:
    """Returns empty string when batch_op is None."""
    from robotsix_auto_mail.server.views.board import _batch_banner_html

    assert _batch_banner_html(None) == ""


def test_batch_banner_html_known_verb_with_progress() -> None:
    """Renders the verb label and done/total when both are integers."""
    from robotsix_auto_mail.server.views.board import _batch_banner_html

    result = _batch_banner_html({"op": "delete", "done": 120, "total": 518})
    assert 'class="batch-banner banner-base"' in result
    assert "Deleting mail: 120/518" in result
    assert "The board will refresh automatically." in result


def test_batch_banner_html_archive_verb() -> None:
    """Renders 'Archiving' for the archive verb."""
    from robotsix_auto_mail.server.views.board import _batch_banner_html

    result = _batch_banner_html({"op": "archive", "done": 3, "total": 10})
    assert "Archiving mail: 3/10" in result


def test_batch_banner_html_no_progress_when_done_none() -> None:
    """Omits the done/total part when *done* is None."""
    from robotsix_auto_mail.server.views.board import _batch_banner_html

    result = _batch_banner_html({"op": "delete", "done": None, "total": 10})
    assert "Deleting mail" in result
    # The progress ":" followed by digits is absent when done is None.
    assert ": " not in result


def test_batch_banner_html_no_progress_when_total_none() -> None:
    """Omits the done/total part when *total* is None."""
    from robotsix_auto_mail.server.views.board import _batch_banner_html

    result = _batch_banner_html({"op": "delete", "done": 5, "total": None})
    assert "Deleting mail" in result
    assert ": " not in result


def test_batch_banner_html_unknown_verb_fallback() -> None:
    """Falls back to 'Processing' when the op verb is not in BATCH_OP_VERB_LABELS."""
    from robotsix_auto_mail.server.views.board import _batch_banner_html

    result = _batch_banner_html({"op": "nuke", "done": 1, "total": 2})
    assert "Processing mail: 1/2" in result


def test_batch_banner_html_bare_running_sentinel() -> None:
    """Handles the bare running sentinel shape: op=None, done=None, total=None."""
    from robotsix_auto_mail.server.views.board import _batch_banner_html

    result = _batch_banner_html({"op": None, "done": None, "total": None})
    assert 'class="batch-banner banner-base"' in result
    assert "Processing mail" in result
    assert ": " not in result
    assert "The board will refresh automatically." in result
