Render HTML body for multipart/alternative emails instead of showing only the plain-text stub.

- Added `core/_sanitize.py` — safe HTML sanitizer that strips scripts, event handlers, remote images, and other active content while preserving structural markup.
- Modified `server/views/detail.py` — `_render_body()` now renders the sanitised HTML body as the primary content when a `text/html` alternative is present, falling back to plain text for HTML-less emails.
- Added CSS in `board.css` for `.email-body` (inherits detail typography) and hides `<img>` elements that slip through sanitization.
- Added `tests/core/test_sanitize.py` (24 tests) and updated `tests/server/test_views_detail_body.py` (6 tests) covering the new behaviour.