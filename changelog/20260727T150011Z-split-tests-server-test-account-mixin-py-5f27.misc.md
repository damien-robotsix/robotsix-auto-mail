Split `tests/server/test_account_mixin.py` (562 lines, 37 tests, 4 classes)
into four per-class modules and extract shared helpers (`_AccountMixinFakeHandler`,
`_make_post_body`) to `tests/server/_test_helpers.py`.
