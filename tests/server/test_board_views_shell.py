"""Unit tests for ``_render_board_page_shell`` from
``robotsix_auto_mail.server.views.board``.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _page_shell_sentinels(**overrides: Any) -> str:
    """Call ``_render_board_page_shell`` with sentinel defaults for each
    keyword argument, overridden by *overrides*."""
    from robotsix_auto_mail.server.views.board import _render_board_page_shell

    defaults: dict[str, Any] = {
        "columns_html": "<div>COLUMNS</div>",
        "triage_running": False,
        "picker_html": "<select>PICKER</select>",
        "account_qs": "",
        "fetch_qs": "?a=1",
        "batch_control_html": "<div>BATCH</div>",
        "data_account_js": False,
    }
    defaults.update(overrides)
    return _render_board_page_shell(**defaults)


# ---------------------------------------------------------------------------
# _render_board_page_shell
# ---------------------------------------------------------------------------


def test_page_shell_injects_columns_html() -> None:
    """The columns_html argument is placed inside the .board wrapper."""
    result = _page_shell_sentinels(columns_html="<div>CUSTOM_COL</div>")
    assert "<div>CUSTOM_COL</div>" in result


def test_page_shell_injects_picker_html() -> None:
    """The picker_html argument appears in the page body."""
    result = _page_shell_sentinels(picker_html="<select>MY_PICKER</select>")
    assert "<select>MY_PICKER</select>" in result


def test_page_shell_injects_batch_control_html() -> None:
    """The batch_control_html argument appears inside #batch-control."""
    result = _page_shell_sentinels(batch_control_html="<div>BATCH_PROGRESS</div>")
    assert '<span id="batch-control"><div>BATCH_PROGRESS</div></span>' in result


def test_page_shell_triage_running_true() -> None:
    """When triage_running is True a triage banner is rendered."""
    result = _page_shell_sentinels(triage_running=True)
    assert 'class="triage-banner banner-base"' in result
    assert "Triage is currently running" in result


def test_page_shell_triage_running_false() -> None:
    """When triage_running is False no triage banner is rendered."""
    result = _page_shell_sentinels(triage_running=False)
    assert 'class="triage-banner"' not in result
    assert "Triage is currently running" not in result


def test_page_shell_data_account_js_true() -> None:
    """data_account_js is True in the JS config when the arg is True."""
    result = _page_shell_sentinels(data_account_js=True)
    # The board-config JSON payload.
    assert '"data_account_js": true' in result


def test_page_shell_data_account_js_false() -> None:
    """data_account_js is False in the JS config when the arg is False."""
    result = _page_shell_sentinels(data_account_js=False)
    assert '"data_account_js": false' in result


def test_page_shell_account_qs_wired() -> None:
    """The account_qs is reflected in the JS config."""
    result = _page_shell_sentinels(account_qs="&account=xyz")
    assert '"account_qs": "&account=xyz"' in result


def test_page_shell_fetch_qs_wired() -> None:
    """The fetch_qs is reflected in the JS config."""
    result = _page_shell_sentinels(fetch_qs="?account=__all__")
    assert '"fetch_qs": "?account=__all__"' in result


def test_page_shell_html5_doctype() -> None:
    """The page shell starts with the HTML5 doctype."""
    result = _page_shell_sentinels()
    assert result.startswith("<!DOCTYPE html>")


def test_page_shell_contains_title() -> None:
    """The page shell has a <title>Mail Board</title>."""
    result = _page_shell_sentinels()
    assert "<title>Mail Board</title>" in result


def test_page_shell_contains_board_js() -> None:
    """The page shell loads board.js and board-auto-mail.js."""
    result = _page_shell_sentinels()
    assert 'src="/static/board.js"' in result
    assert 'src="/static/board-auto-mail.js"' in result


def test_page_shell_contains_side_panel() -> None:
    """The page shell includes auto-mail's side-panel skeleton."""
    result = _page_shell_sentinels()
    assert 'class="side-panel"' in result
    assert 'id="side-panel"' in result
    assert 'class="close-btn"' in result
