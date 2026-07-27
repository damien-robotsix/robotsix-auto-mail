"""Unit tests for ``_handle_batch_archive_folder`` (subfolder scoping)."""

from __future__ import annotations

from unittest import mock

from tests.server._test_helpers import _BatchFakeHandler


class TestHandleBatchArchiveFolder:
    """Unit tests for ``_handle_batch_archive_folder``."""

    def test_scoped_subfolder_delegates_to_batch_archive(
        self, tmp_db_path: str
    ) -> None:
        """``_handle_batch_archive_folder`` reads the ``folder`` field from
        the form body and delegates to ``_handle_batch_archive`` with that
        subfolder."""
        handler = _BatchFakeHandler(tmp_db_path)
        handler.headers = mock.MagicMock()
        handler.headers.get.return_value = "0"
        handler.rfile = mock.MagicMock()
        handler.rfile.read.return_value = b"folder=Receipts%2F2025"

        with mock.patch.object(handler, "_handle_batch_archive") as mock_archive:
            handler._handle_batch_archive_folder()

        mock_archive.assert_called_once_with(subfolder="Receipts/2025")

    def test_empty_folder_passes_none_subfolder(self, tmp_db_path: str) -> None:
        """An empty ``folder`` field delegates to ``_handle_batch_archive``
        with ``subfolder=None`` (archive the whole column)."""
        handler = _BatchFakeHandler(tmp_db_path)
        handler.headers = mock.MagicMock()
        handler.headers.get.return_value = "0"
        handler.rfile = mock.MagicMock()
        handler.rfile.read.return_value = b"folder="

        with mock.patch.object(handler, "_handle_batch_archive") as mock_archive:
            handler._handle_batch_archive_folder()

        mock_archive.assert_called_once_with(subfolder="")
