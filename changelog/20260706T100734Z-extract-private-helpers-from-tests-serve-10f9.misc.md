Extracted 25 private helper functions and 2 utility classes from
``tests/server/conftest.py`` into a new ``tests/server/conftest_helpers.py``
module.  ``conftest.py`` now contains only the 5 ``@pytest.fixture``
definitions (~90 lines), importing the helpers it needs from the new module.
