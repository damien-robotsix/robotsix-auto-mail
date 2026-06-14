"""Tests for the _parse_archive_structure helper."""

from __future__ import annotations

# ===========================================================================
# _parse_archive_structure unit tests
# ===========================================================================


def test_parse_archive_structure_none_input_returns_defaults() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    folders, delim, root = _parse_archive_structure(None, "my-archive")
    assert folders == set()
    assert delim == "/"
    assert root == "my-archive"


def test_parse_archive_structure_empty_string_falls_back() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    folders, delim, root = _parse_archive_structure("", "my-archive")
    assert folders == set()
    assert delim == "/"
    assert root == "my-archive"


def test_parse_archive_structure_malformed_json_falls_back() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    folders, delim, root = _parse_archive_structure("not json{", "my-archive")
    assert folders == set()
    assert delim == "/"
    assert root == "my-archive"


def test_parse_archive_structure_old_format_bare_list() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    raw = '["a", "b", "c"]'
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == {"a", "b", "c"}
    assert delim == "/"
    assert root == "a"


def test_parse_archive_structure_old_format_single_element_list() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    raw = '["only"]'
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == {"only"}
    assert delim == "/"
    assert root == "only"


def test_parse_archive_structure_old_format_empty_list() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    raw = "[]"
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == set()
    assert delim == "/"
    assert root == "my-archive"


def test_parse_archive_structure_new_format_with_delimiter_and_folders() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    raw = '{"delimiter": ".", "folders": ["x", "y", "z"]}'
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == {"x", "y", "z"}
    assert delim == "."
    assert root == "x"


def test_parse_archive_structure_new_format_default_delimiter() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    raw = '{"folders": ["p", "q"]}'
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == {"p", "q"}
    assert delim == "/"
    assert root == "p"


def test_parse_archive_structure_new_format_empty_folders() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    raw = '{"delimiter": "/", "folders": []}'
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == set()
    assert delim == "/"
    assert root == "my-archive"


def test_parse_archive_structure_new_format_single_folder() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    raw = '{"delimiter": "/", "folders": ["single"]}'
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == {"single"}
    assert delim == "/"
    assert root == "single"


def test_parse_archive_structure_new_format_missing_folders_key_falls_back() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    raw = '{"delimiter": "/"}'
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == set()
    assert delim == "/"
    assert root == "my-archive"


def test_parse_archive_structure_non_list_non_dict_falls_back() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    assert _parse_archive_structure("42", "my-archive") == (set(), "/", "my-archive")
    assert _parse_archive_structure('"a string"', "my-archive") == (
        set(),
        "/",
        "my-archive",
    )


def test_parse_archive_structure_extra_keys_in_new_format_ignored() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    raw = '{"delimiter": ".", "folders": ["a", "b"], "extra": "ignored"}'
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == {"a", "b"}
    assert delim == "."
    assert root == "a"


def test_parse_archive_structure_typeerror_falls_back() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    # JSON with null for folders triggers TypeError on dict iteration
    raw = '{"folders": null}'
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == set()
    assert delim == "/"
    assert root == "my-archive"
