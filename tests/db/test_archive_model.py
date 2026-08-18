"""Tests for the archive model types and lazy provider import."""

from robotsix_auto_mail.db.archive import ArchiveError, ArchiveStructure


# ---------------------------------------------------------------------------
# ArchiveStructure
# ---------------------------------------------------------------------------


def test_archive_structure_defaults_empty() -> None:
    """folders defaults to an empty list."""
    assert ArchiveStructure().folders == []


def test_archive_structure_accepts_folders() -> None:
    """folders is populated from input."""
    s = ArchiveStructure(folders=["a", "a/b"])
    assert s.folders == ["a", "a/b"]


# ---------------------------------------------------------------------------
# ArchiveError
# ---------------------------------------------------------------------------


def test_archive_error_is_exception() -> None:
    err = ArchiveError("boom")
    assert isinstance(err, Exception)
    assert str(err) == "boom"


# ---------------------------------------------------------------------------
# Lazy provider import — deterministic path must not bind the extra
# ---------------------------------------------------------------------------


def test_provider_not_bound_at_module_level() -> None:
    """Importing the module must not require a concrete provider extra.

    The provider is resolved lazily inside ``determine_archive_structure``,
    so it must not be a module-level attribute of ``archive``.
    """
    import robotsix_auto_mail.db.archive as archive_mod

    assert not hasattr(archive_mod, "get_provider_for_identifier")
