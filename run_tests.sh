cd /repo && python -m pytest tests/server/test_board_handler_move.py::test_move_to_calendar_writes_calendar_columns -xvs 2>&1 | tail -30
