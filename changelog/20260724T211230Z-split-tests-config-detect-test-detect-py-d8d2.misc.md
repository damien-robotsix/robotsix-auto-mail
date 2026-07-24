Split `tests/config/detect/test_detect.py` (679 lines) into four domain-focused
modules: `test_detect_models.py`, `test_detect_provider.py`,
`test_detect_autoconfig.py`, and `test_detect_consistency.py`, with shared
helpers moved to `tests/config/detect/conftest.py`.
