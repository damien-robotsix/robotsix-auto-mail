"""Tests for the _is_safe_redirect_path helper."""

from __future__ import annotations

# ===========================================================================
# _is_safe_redirect_path unit tests
# ===========================================================================


def test_is_safe_redirect_path_simple_root() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert _is_safe_redirect_path("/")


def test_is_safe_redirect_path_with_path_segments() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert _is_safe_redirect_path("/path/to/page")


def test_is_safe_redirect_path_with_query_and_fragment() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert _is_safe_redirect_path("/path?q=1#frag")


def test_is_safe_redirect_path_rejects_protocol_relative() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert not _is_safe_redirect_path("//evil.com")


def test_is_safe_redirect_path_rejects_backslash_trick() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert not _is_safe_redirect_path("/\\evil")


def test_is_safe_redirect_path_rejects_empty_string() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert not _is_safe_redirect_path("")


def test_is_safe_redirect_path_rejects_no_leading_slash() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert not _is_safe_redirect_path("board")
    assert not _is_safe_redirect_path("../etc")


def test_is_safe_redirect_path_rejects_absolute_url() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert not _is_safe_redirect_path("http://evil.com")
    assert not _is_safe_redirect_path("https://evil.com/path")


def test_is_safe_redirect_path_rejects_crlf_injection() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert not _is_safe_redirect_path("/board\r\nSet-Cookie: pwned=1")
    assert not _is_safe_redirect_path("/board\nX-Injected: true")
    assert not _is_safe_redirect_path("/board\rX-Injected: true")


def test_is_safe_redirect_path_rejects_other_control_characters() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert not _is_safe_redirect_path("/path\x00")
    assert not _is_safe_redirect_path("/path\x1f")
    assert not _is_safe_redirect_path("/path\x7f")  # DEL


def test_is_safe_redirect_path_rejects_null_byte() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert not _is_safe_redirect_path("/safe\x00hidden")


def test_is_safe_redirect_path_accepts_typical_valid_paths() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert _is_safe_redirect_path("/board")
    assert _is_safe_redirect_path("/email/some-id")
    assert _is_safe_redirect_path("/email/some-id?embed=1")


# ===========================================================================
# _parse_archive_structure unit tests
# ===========================================================================
