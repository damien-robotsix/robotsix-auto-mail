"""Unit tests for ``_build_board_html`` and ``_build_global_board_html``
from ``robotsix_auto_mail.server.views``.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _dummy_mail_config(db_path: str) -> Any:
    """A minimal MailConfig bound to *db_path*."""
    from robotsix_auto_mail.config import MailConfig

    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="u@example.com",
        password="p",
        db_path=db_path,
        archive_enabled=False,
        triage_on_ingest=False,
    )


# ---------------------------------------------------------------------------
# _build_board_html  (via monkeypatching helpers)
# ---------------------------------------------------------------------------


def test_build_board_html_patches_through() -> None:
    """_build_board_html assembles the shell from its content/banner/shell
    helpers (verified by patching each to a unique sentinel)."""
    from robotsix_auto_mail.server.views import _build_board_html

    sentinel_content = {
        "columns_html": "<div>FAKE_COLS</div>",
        "triage_running": True,
        "batch_op": {"op": "delete", "done": 1, "total": 2},
    }
    sentinel_shell = "<html>SHELL_SENTINEL</html>"

    with (
        mock.patch(
            "robotsix_auto_mail.server.views.board._build_board_content",
            return_value=sentinel_content,
        ),
        mock.patch(
            "robotsix_auto_mail.server.views.board._render_board_page_shell",
            return_value=sentinel_shell,
        ),
    ):
        result = _build_board_html("/fake/path.db")

    assert result == sentinel_shell


def test_build_board_html_single_account_no_picker() -> None:
    """_build_board_html with no accounts yields an empty picker_html and
    data_account_js=False."""
    from robotsix_auto_mail.server.views import _build_board_html

    with mock.patch(
        "robotsix_auto_mail.server.views.board._build_board_content",
        return_value={
            "columns_html": "",
            "triage_running": False,
            "batch_op": None,
        },
    ):
        with mock.patch(
            "robotsix_auto_mail.server.views.board._render_board_page_shell",
        ) as mock_shell:
            _build_board_html("/fake/path.db")

    kwargs = mock_shell.call_args.kwargs
    assert kwargs["picker_html"] == ""
    assert kwargs["data_account_js"] is False
    assert kwargs["account_qs"] == ""


def test_build_board_html_multi_account_has_picker_and_qs() -> None:
    """_build_board_html with ≥2 accounts renders a picker and wires
    account_qs/fetch_qs."""
    from robotsix_auto_mail.server.views import _build_board_html

    accounts = MailAccountsConfig(
        accounts=(
            MailAccount(
                account_id="A",
                config=_dummy_mail_config("/fake/a.db"),
                label="Acc A",
            ),
            MailAccount(
                account_id="B",
                config=_dummy_mail_config("/fake/b.db"),
                label="Acc B",
            ),
        ),
        default_account_id="A",
    )

    with mock.patch(
        "robotsix_auto_mail.server.views.board._build_board_content",
        return_value={
            "columns_html": "",
            "triage_running": False,
            "batch_op": None,
        },
    ):
        with mock.patch(
            "robotsix_auto_mail.server.views.board._render_board_page_shell",
        ) as mock_shell:
            _build_board_html("/fake/a.db", accounts=accounts, current_account_id="A")

    kwargs = mock_shell.call_args.kwargs
    assert kwargs["picker_html"] != ""
    assert "All mailboxes" in kwargs["picker_html"]
    assert "Acc A" in kwargs["picker_html"]
    assert "Acc B" in kwargs["picker_html"]
    # With a current_account_id set, account_qs and fetch_qs are wired.
    assert "account=" in kwargs["account_qs"]
    assert "?account=" in kwargs["fetch_qs"]


# ---------------------------------------------------------------------------
# _build_global_board_html  (via monkeypatching helpers)
# ---------------------------------------------------------------------------


def test_build_global_board_html_patches_through() -> None:
    """_build_global_board_html assembles the shell via its helpers."""
    from robotsix_auto_mail.server.views import _build_global_board_html

    sentinel_content = {
        "columns_html": "<div>GLOBAL_COLS</div>",
        "triage_running": False,
        "batch_op": None,
    }
    sentinel_shell = "<html>GLOBAL_SHELL</html>"

    accounts = MailAccountsConfig(
        accounts=(
            MailAccount(
                account_id="A",
                config=_dummy_mail_config("/fake/a.db"),
                label="A One",
            ),
        ),
        default_account_id="A",
    )

    with (
        mock.patch(
            "robotsix_auto_mail.server.views.board._build_global_board_content",
            return_value=sentinel_content,
        ),
        mock.patch(
            "robotsix_auto_mail.server.views.board._render_board_page_shell",
            return_value=sentinel_shell,
        ),
    ):
        result = _build_global_board_html(accounts)

    assert result == sentinel_shell


def test_build_global_board_html_data_account_and_picker() -> None:
    """_build_global_board_html passes data_account_js=True and a picker
    with 'All mailboxes' selected."""
    from robotsix_auto_mail.server.views import _build_global_board_html

    accounts = MailAccountsConfig(
        accounts=(
            MailAccount(
                account_id="X",
                config=_dummy_mail_config("/fake/x.db"),
                label="X Label",
            ),
            MailAccount(
                account_id="Y",
                config=_dummy_mail_config("/fake/y.db"),
                label=None,
            ),
        ),
        default_account_id="X",
    )

    with mock.patch(
        "robotsix_auto_mail.server.views.board._build_global_board_content",
        return_value={
            "columns_html": "",
            "triage_running": False,
            "batch_op": None,
        },
    ):
        with mock.patch(
            "robotsix_auto_mail.server.views.board._render_board_page_shell",
        ) as mock_shell:
            _build_global_board_html(accounts)

    kwargs = mock_shell.call_args.kwargs
    assert kwargs["data_account_js"] is True
    assert kwargs["account_qs"] == ""  # no page-level account_qs
    assert kwargs["fetch_qs"] == "?account=__all__"
    picker = kwargs["picker_html"]
    assert "All mailboxes" in picker
    assert '__all__" selected' in picker
    assert "X Label" in picker
    assert "Y" in picker  # falls back to account_id when label is None


def test_build_global_board_html_batch_banner_wired() -> None:
    """The batch banner is wired through when batch_op is present."""
    from robotsix_auto_mail.server.views import _build_global_board_html

    accounts = MailAccountsConfig(
        accounts=(
            MailAccount(
                account_id="Z",
                config=_dummy_mail_config("/fake/z.db"),
                label="Z",
            ),
        ),
        default_account_id="Z",
    )

    with mock.patch(
        "robotsix_auto_mail.server.views.board._build_global_board_content",
        return_value={
            "columns_html": "",
            "triage_running": False,
            "batch_op": {"op": "archive", "done": 5, "total": 20},
        },
    ):
        with mock.patch(
            "robotsix_auto_mail.server.views.board._render_board_page_shell",
        ) as mock_shell:
            _build_global_board_html(accounts)

    kwargs = mock_shell.call_args.kwargs
    assert "Archiving mail: 5/20" in kwargs["batch_control_html"]
