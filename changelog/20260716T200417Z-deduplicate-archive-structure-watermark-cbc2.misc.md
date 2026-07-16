De-duplicate archive-structure watermark parsing in `board_data.py` by calling the shared `_parse_archive_structure` helper instead of inlining identical JSON parsing logic.
