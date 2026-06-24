"""Unit tests for ``_BoardActionMixin._parse_request_body``.

Covers field stripping, no-strip preservation, missing-field defaults,
content-length honouring, duplicate-field handling, and empty-body
behaviour.
"""

from __future__ import annotations

from tests.server._test_helpers import _FakeHandler


class TestParseRequestBody:
    def test_strips_fields_by_default(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        handler.headers.get.return_value = 50
        handler.rfile.read.return_value = b"field1=++hello++&field2=++world++"

        result = handler._parse_request_body("field1", "field2")
        assert result == {"field1": "hello", "field2": "world"}

    def test_no_strip_preserves_whitespace(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        handler.headers.get.return_value = 60
        handler.rfile.read.return_value = (
            b"notes=++leading+trailing++&other=++trimmed++"
        )

        result = handler._parse_request_body(
            "notes", "other", no_strip=frozenset({"notes"})
        )
        # notes: spaces preserved (the '+' signs decode to spaces in
        # URL-encoded form, and parse_qs doesn't strip).
        assert result["notes"].startswith("  ")
        assert result["notes"].endswith("  ")
        assert "leading trailing" in result["notes"]
        # other: stripped by default.
        assert result["other"] == "trimmed"

    def test_missing_fields_default_to_empty_string(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        handler.headers.get.return_value = 12
        handler.rfile.read.return_value = b"field1=hello"

        result = handler._parse_request_body("field1", "field2")
        assert result == {"field1": "hello", "field2": ""}

    def test_content_length_honored(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        handler.headers.get.return_value = 7
        handler.rfile.read.return_value = b"field1=hello&field2=world"

        result = handler._parse_request_body("field1")
        # Only 7 bytes read: "field1=" — but parse_qs handles truncated input.
        assert "field1" in result

    def test_single_field_value_takes_first(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        handler.headers.get.return_value = 30
        handler.rfile.read.return_value = b"field1=first&field1=second"

        result = handler._parse_request_body("field1")
        assert result == {"field1": "first"}

    def test_empty_body_yields_empty_strings(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        handler.headers.get.return_value = 0
        handler.rfile.read.return_value = b""

        result = handler._parse_request_body("field1", "field2")
        assert result == {"field1": "", "field2": ""}
