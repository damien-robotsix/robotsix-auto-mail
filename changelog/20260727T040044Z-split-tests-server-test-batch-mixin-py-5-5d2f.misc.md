Split ``tests/server/test_batch_mixin.py`` into four focused modules:
``test_batch_mixin_delete.py``, ``test_batch_mixin_delete_aggregate.py``,
``test_batch_mixin_archive_folder.py``, and ``test_batch_mixin_archive.py``.
Extracted the shared ``_BatchFakeHandler`` to ``tests/server/_test_helpers.py``
alongside the existing ``_DraftMixinFakeHandler``.
