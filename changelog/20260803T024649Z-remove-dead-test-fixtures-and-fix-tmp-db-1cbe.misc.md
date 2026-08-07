Removed dead `_patch_serve_board_deps` autouse fixture and renamed
`tmp_db_path` to `_fake_db_path` in `tests/server/_view_mixin_helpers.py`
to eliminate collision with root conftest. Deleted empty `tests/db/conftest.py`.
