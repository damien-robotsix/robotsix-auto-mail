"""Unit tests for ``_BoardViewMixin._serve_archive_proposal`` and
``_serve_archive_folders``."""

from __future__ import annotations

from unittest import mock

import pytest

from tests.server._view_mixin_helpers import _FakeHandler

pytest_plugins = ["tests.server._view_mixin_helpers"]


class TestServeArchiveProposal:
    @pytest.fixture(autouse=True)
    def _patch_imports(
        self,
        mock_init_db: mock.MagicMock,
        mock_get_record_by_message_id: mock.MagicMock,
        mock_get_archive_subfolder: mock.MagicMock,
        mock_load_archive_overrides: mock.MagicMock,
        mock_load_llm_archive_hints: mock.MagicMock,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        pass

    def test_404_when_record_not_found(
        self,
        tmp_db_path: str,
        mock_get_record_by_message_id: mock.MagicMock,
    ) -> None:
        mock_get_record_by_message_id.return_value = None
        handler = _FakeHandler(tmp_db_path, path="/archive-proposal/test%40example.com")
        handler._serve_archive_proposal()
        handler._not_found.assert_called_once()

    def test_override_source(
        self,
        tmp_db_path: str,
        mock_get_record_by_message_id: mock.MagicMock,
        mock_get_archive_subfolder: mock.MagicMock,
        mock_load_archive_overrides: mock.MagicMock,
        mock_load_llm_archive_hints: mock.MagicMock,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        mid = "override@example.com"
        mock_get_record_by_message_id.return_value = mock.MagicMock()
        mock_get_archive_subfolder.return_value = "Projects/Widgets"
        mock_load_archive_overrides.return_value = {mid: "Projects/Widgets"}
        mock_load_llm_archive_hints.return_value = {}
        mock_get_watermark.return_value = '{"delimiter":"/","folders":["Root","Root/Projects","Root/Projects/Widgets"]}'
        mock_parse_archive_structure.return_value = (
            {"Root", "Root/Projects", "Root/Projects/Widgets"},
            "/",
            "Root",
        )
        handler = _FakeHandler(
            tmp_db_path,
            path=f"/archive-proposal/{mid}",
        )
        handler._serve_archive_proposal()
        handler._serve_json.assert_called_once_with(
            {
                "subfolder": "Projects/Widgets",
                "archive_root": mock.ANY,
                "folder_exists": True,
                "overridden": True,
                "source": "override",
            }
        )

    def test_llm_source(
        self,
        tmp_db_path: str,
        mock_get_record_by_message_id: mock.MagicMock,
        mock_get_archive_subfolder: mock.MagicMock,
        mock_load_archive_overrides: mock.MagicMock,
        mock_load_llm_archive_hints: mock.MagicMock,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        mid = "llm@example.com"
        mock_get_record_by_message_id.return_value = mock.MagicMock()
        mock_get_archive_subfolder.return_value = "Archive/2024"
        mock_load_archive_overrides.return_value = {}
        mock_load_llm_archive_hints.return_value = {mid: "Archive/2024"}
        mock_get_watermark.return_value = (
            '{"delimiter":"/","folders":["Root","Root/Archive","Root/Archive/2024"]}'
        )
        mock_parse_archive_structure.return_value = (
            {"Root", "Root/Archive", "Root/Archive/2024"},
            "/",
            "Root",
        )
        handler = _FakeHandler(
            tmp_db_path,
            path=f"/archive-proposal/{mid}",
        )
        handler._serve_archive_proposal()
        handler._serve_json.assert_called_once_with(
            {
                "subfolder": "Archive/2024",
                "archive_root": mock.ANY,
                "folder_exists": True,
                "overridden": False,
                "source": "llm",
            }
        )

    def test_rule_source(
        self,
        tmp_db_path: str,
        mock_get_record_by_message_id: mock.MagicMock,
        mock_get_archive_subfolder: mock.MagicMock,
        mock_load_archive_overrides: mock.MagicMock,
        mock_load_llm_archive_hints: mock.MagicMock,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        mid = "rule@example.com"
        mock_get_record_by_message_id.return_value = mock.MagicMock()
        mock_get_archive_subfolder.return_value = "Misc"
        mock_load_archive_overrides.return_value = {}
        mock_load_llm_archive_hints.return_value = {}
        mock_get_watermark.return_value = (
            '{"delimiter":"/","folders":["Root","Root/Misc"]}'
        )
        mock_parse_archive_structure.return_value = (
            {"Root", "Root/Misc"},
            "/",
            "Root",
        )
        handler = _FakeHandler(
            tmp_db_path,
            path=f"/archive-proposal/{mid}",
        )
        handler._serve_archive_proposal()
        handler._serve_json.assert_called_once_with(
            {
                "subfolder": "Misc",
                "archive_root": mock.ANY,
                "folder_exists": True,
                "overridden": False,
                "source": "rule",
            }
        )

    def test_folder_exists_with_custom_delimiter(
        self,
        tmp_db_path: str,
        mock_get_record_by_message_id: mock.MagicMock,
        mock_get_archive_subfolder: mock.MagicMock,
        mock_load_archive_overrides: mock.MagicMock,
        mock_load_llm_archive_hints: mock.MagicMock,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        mid = "rule@example.com"
        mock_get_record_by_message_id.return_value = mock.MagicMock()
        mock_get_archive_subfolder.return_value = "A/B/C"
        mock_load_archive_overrides.return_value = {}
        mock_load_llm_archive_hints.return_value = {}
        mock_get_watermark.return_value = (
            '{"delimiter":"/","folders":["Root","Root/A","Root/A/B","Root/A/B/C"]}'
        )
        mock_parse_archive_structure.return_value = (
            {"Root", "Root/A", "Root/A/B", "Root/A/B/C"},
            "/",
            "Root",
        )
        handler = _FakeHandler(
            tmp_db_path,
            path=f"/archive-proposal/{mid}",
        )
        handler._serve_archive_proposal()
        handler._serve_json.assert_called_once_with(
            {
                "subfolder": "A/B/C",
                "archive_root": mock.ANY,
                "folder_exists": True,
                "overridden": False,
                "source": "rule",
            }
        )

    def test_none_subfolder(
        self,
        tmp_db_path: str,
        mock_get_record_by_message_id: mock.MagicMock,
        mock_get_archive_subfolder: mock.MagicMock,
        mock_load_archive_overrides: mock.MagicMock,
        mock_load_llm_archive_hints: mock.MagicMock,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        mock_get_record_by_message_id.return_value = mock.MagicMock()
        mock_get_archive_subfolder.return_value = ""
        mock_load_archive_overrides.return_value = {}
        mock_load_llm_archive_hints.return_value = {}
        mock_get_watermark.return_value = '{"delimiter":"/","folders":["Root"]}'
        mock_parse_archive_structure.return_value = (
            {"Root"},
            "/",
            "Root",
        )
        handler = _FakeHandler(tmp_db_path, path="/archive-proposal/any@example.com")
        handler._serve_archive_proposal()
        call_args = handler._serve_json.call_args[0][0]
        assert call_args["subfolder"] == ""
        assert (
            call_args["folder_exists"] is True
        )  # full_path == effective_root == "Root"


class TestServeArchiveFolders:
    @pytest.fixture(autouse=True)
    def _patch_imports(
        self,
        mock_init_db: mock.MagicMock,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        pass

    def test_aggregate_short_circuit(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path, _aggregate=True)
        handler._serve_archive_folders()
        handler._serve_json.assert_called_once_with({"delimiter": "/", "folders": []})

    def test_returns_sorted_subfolders(
        self,
        tmp_db_path: str,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        mock_get_watermark.return_value = "ignored"
        mock_parse_archive_structure.return_value = (
            {"Root", "Root/2024", "Root/2023", "Root/2024/Q1"},
            "/",
            "Root",
        )
        handler = _FakeHandler(tmp_db_path, _aggregate=False)
        handler._serve_archive_folders()
        handler._serve_json.assert_called_once_with(
            {"delimiter": "/", "folders": ["2023", "2024", "2024/Q1"]}
        )

    def test_strips_effective_root_and_translates_delimiter(
        self,
        tmp_db_path: str,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        mock_get_watermark.return_value = "ignored"
        mock_parse_archive_structure.return_value = (
            {"INBOX.Archive", "INBOX.Archive.2024", "INBOX.Archive.2024.Q1"},
            ".",
            "INBOX.Archive",
        )
        handler = _FakeHandler(tmp_db_path, _aggregate=False)
        handler._serve_archive_folders()
        handler._serve_json.assert_called_once_with(
            {"delimiter": "/", "folders": ["2024", "2024/Q1"]}
        )

    def test_empty_folders(
        self,
        tmp_db_path: str,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        mock_get_watermark.return_value = "ignored"
        mock_parse_archive_structure.return_value = (
            {"Root"},
            "/",
            "Root",
        )
        handler = _FakeHandler(tmp_db_path, _aggregate=False)
        handler._serve_archive_folders()
        handler._serve_json.assert_called_once_with({"delimiter": "/", "folders": []})
