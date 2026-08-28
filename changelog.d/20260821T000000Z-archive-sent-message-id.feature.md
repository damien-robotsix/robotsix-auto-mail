Add `message_id` to archive and sent folder message listings.

- `GET /archive/<folder>/messages` and `GET /sent/messages` now include a
  `message_id` field (the RFC 5322 Message-ID header) on every message object,
  alongside the existing `uid`, `subject`, `from`, `to`, `date`, `size`, and
  `flags`.
- Additive and backward compatible: the field is new, no existing fields or
  behavior change.
- Unblocks the existing `POST /email/<message_id>/attachments/to-file-hub`
  endpoint for messages discovered via the archive or Sent listings (which
  previously exposed only the IMAP UID, not the Message-ID).
