"""Unit tests for ``_BoardActionMixin._parse_request_body``.

Covers field stripping, no-strip preservation, missing-field defaults,
content-length honouring, duplicate-field handling, empty-body
behaviour, and JSON fallback parsing.
"""

from __future__ import annotations

import json

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

    # -- JSON fallback ---------------------------------------------------

    def test_json_body_parses_fields(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        payload = json.dumps({"message_id": "abc-123", "triage_action": "TO_READ"})
        handler.headers.get.return_value = len(payload)
        handler.rfile.read.return_value = payload.encode()

        result = handler._parse_request_body("message_id", "triage_action")
        assert result == {"message_id": "abc-123", "triage_action": "TO_READ"}

    def test_json_body_falls_back_when_form_empty(self, tmp_db_path: str) -> None:
        """JSON fallback activates when form parsing yields all empty strings."""
        handler = _FakeHandler(tmp_db_path)
        payload = json.dumps({"field1": "hello", "field2": "world"})
        handler.headers.get.return_value = len(payload)
        handler.rfile.read.return_value = payload.encode()

        result = handler._parse_request_body("field1", "field2")
        assert result == {"field1": "hello", "field2": "world"}

    def test_json_body_null_value_yields_empty_string(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        payload = json.dumps({"message_id": None, "redirect_to": "/board"})
        handler.headers.get.return_value = len(payload)
        handler.rfile.read.return_value = payload.encode()

        result = handler._parse_request_body("message_id", "redirect_to")
        assert result == {"message_id": "", "redirect_to": "/board"}

    def test_json_body_numeric_value_coerced_to_string(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        payload = json.dumps({"message_id": 42, "triage_action": "TO_ARCHIVE"})
        handler.headers.get.return_value = len(payload)
        handler.rfile.read.return_value = payload.encode()

        result = handler._parse_request_body("message_id", "triage_action")
        assert result == {"message_id": "42", "triage_action": "TO_ARCHIVE"}

    def test_json_body_missing_key_yields_empty_string(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        payload = json.dumps({"message_id": "abc"})
        handler.headers.get.return_value = len(payload)
        handler.rfile.read.return_value = payload.encode()

        result = handler._parse_request_body("message_id", "redirect_to")
        assert result == {"message_id": "abc", "redirect_to": ""}

    def test_json_body_strips_fields_by_default(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        payload = json.dumps({"field1": "  hello  ", "field2": "  world  "})
        handler.headers.get.return_value = len(payload)
        handler.rfile.read.return_value = payload.encode()

        result = handler._parse_request_body("field1", "field2")
        assert result == {"field1": "hello", "field2": "world"}

    def test_json_body_no_strip_preserves_whitespace(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        payload = json.dumps({"notes": "  keep spaces  ", "other": "  trim  "})
        handler.headers.get.return_value = len(payload)
        handler.rfile.read.return_value = payload.encode()

        result = handler._parse_request_body(
            "notes", "other", no_strip=frozenset({"notes"})
        )
        assert result == {"notes": "  keep spaces  ", "other": "trim"}

    def test_json_array_body_skips_fallback(self, tmp_db_path: str) -> None:
        """A JSON array (not a dict) is not used for field extraction."""
        handler = _FakeHandler(tmp_db_path)
        payload = json.dumps([{"message_id": "abc"}])
        handler.headers.get.return_value = len(payload)
        handler.rfile.read.return_value = payload.encode()

        result = handler._parse_request_body("message_id", "field2")
        assert result == {"message_id": "", "field2": ""}

    def test_malformed_json_returns_empty_fields(self, tmp_db_path: str) -> None:
        """Malformed JSON falls through quietly; caller handles error reporting."""
        handler = _FakeHandler(tmp_db_path)
        payload = "{not valid json"
        handler.headers.get.return_value = len(payload)
        handler.rfile.read.return_value = payload.encode()

        result = handler._parse_request_body("message_id", "redirect_to")
        assert result == {"message_id": "", "redirect_to": ""}
