---
name: robotsix-auto-mail
description: Mail triage and archive-proposal board — read board state, inspect emails, and manage archive decisions over HTTP.
---

robotsix-auto-mail is a deployable mail-triage component. It ingests mail
from IMAP servers, runs an LLM triage agent that classifies each message
into a board column (Inbox, To Answer, To Archive, To Delete, etc.), and
exposes a web board where operators can review and act on triage decisions.

## Read-only HTTP API (safe — no side effects)

All GET endpoints below are read-only and safe to call without confirmation.

### Account listing

- **`GET /accounts`** → JSON `{"accounts":[{"id":"ROBOTSIX","address":"…","label":"…","healthy":true},…]}`.
  Lists every configured mail account with its id, email address, optional
  human-readable label, and health status.  `healthy` is `true` when the
  last IMAP/SMTP connectivity probe succeeded, `false` when it failed, and
  `null` when no probe has run yet.  Read-only — no side effects.

### Board state

- **`GET /board-content`** → JSON `{"columns_html":"…","triage_running":bool,"batch_op":…,"health":…}`
  Returns the full board payload: rendered column HTML, triage-agent status,
  batch-operation progress (if any), and per-account health watermark.

- **`GET /board-content?format=json`** → JSON `{"columns":{…},"triage_running":bool}`.
  Same board data as ``/board-content`` but in structured form (no HTML).
  Each card object carries ``message_id``, ``subject``, ``from``, ``date``,
  ``status`` (the triage column, e.g. ``INBOX``, ``TO_ARCHIVE``), and
  ``account`` (the owning account id).  Cards are grouped by triage column.
  Optional ``?account=<id>`` filters to a single account; an unknown id
  returns 404.  Omitted ``?account=`` returns all accounts.  Read-only —
  no side effects.  This is the preferred endpoint for programmatic triage
  when per-column grouping is needed.

- **`GET /board-cards?account=<id>`** → JSON `{"cards":[…],"account":"<id>"}`.
  Returns a flat JSON array of every board card with structured fields:
  `message_id`, `uid` (nullable), `subject`, `from`, `date`, `column`
  (triage action), `proposed_archive_subfolder`, and `account`.
  The `?account=` query parameter is **required** — an unknown/mistyped
  account returns 404 (never silently falls back to the default account).
  Optional `?column=<action>` (or `?status=<action>`) filters to a single
  triage column (e.g. `TO_ARCHIVE`, `INBOX`, `TO_ANSWER`).
  No HTML anywhere in the response — this is the preferred endpoint for
  programmatic triage (chat agent, scripts, grouping by destination).

### Email inspection

- **`GET /email/<message_id>`** → HTML detail page for one message.
  Optional `?embed=1` strips the page chrome for embedding.

- **`GET /email/<message_id>/status`** → plain-text triage action name
  (e.g. `INBOX`, `TO_ARCHIVE`, `DRAFT_READY`). Returns 404 for unknown IDs.

### Archive layout

- **`GET /archive-folders`** → JSON `{"delimiter":"/","folders":[…]}`.
  Lists available IMAP archive subfolders.

- **`GET /archive-proposal/<message_id>`** → JSON `{"subfolder":"…","archive_root":"…","folder_exists":bool,"overridden":bool,"source":"…"}`.
  Shows the effective archive subfolder for a message (source: `override`, `llm`, or `rule`).

- **`GET /archive/<folder>/messages`** → JSON `{"folder":"…","total":N,"shown":N,"messages":[…]}`.
  Lists messages inside an archive subfolder. Each message object contains `uid`, `subject`, `from`, `date`, `size`, and `flags`.  Accepts optional `?limit=N` (default 500, max 2000).  Returns 404 when the folder does not exist.

### Liveness

- **`GET /health`** → JSON `{"status":"ok"}` 200. Liveness check only.

## Safety rules

- **Read operations** (all `GET` endpoints above) are always safe — they
  never modify state and may be called freely.

- **State-mutating operations** require **explicit user confirmation**
  before execution. These include:
  - Accepting or rejecting an archive proposal (`POST /move`,
    `POST /archive-proposal`).
  - Sending mail (`POST /send-draft`).
  - Any batch operation (`POST /batch-delete`, `POST /batch-archive`,
    `POST /batch-archive-folder`).
  - Deleting or archiving individual messages (`POST /delete`,
    `POST /archive`).
  - Moving messages between archive folders (`POST /archive-move`).
  - Deleting archive folders (`POST /archive-delete`).
  - Deleting archived messages (`POST /archive-message-delete`).
  - Renaming archive folders (`POST /archive-rename`).
  - Triggering triage or reconcile runs (`POST /run-triage`,
    `POST /reconcile`, `POST /force-triage-column`).
  - Saving or generating drafts (`POST /save-draft`, `POST /generate-draft`).
  - Config mutations (`PUT /config`, `POST /config/rollback`,
    `POST /config-sync`).
  - Pushing email attachments to file-hub
    (`POST /email/<message_id>/attachments/to-file-hub`).
  - Composing a new outgoing draft
    (`POST /compose-draft`).

  Before executing any POST/PUT, confirm with the user and explain what
  the operation will do.

## Archive move (state-mutating, requires confirmation)

- **`POST /archive-move`** — Move a single message from one archive
  subfolder to another.  Requires operator confirmation per the safety
  rules above.

  JSON request body:
  ```json
  {
    "message_id": "<Message-ID header>",
    "source_folder": "<current archive subfolder path>",
    "target_subfolder": "<destination archive subfolder path>"
  }
  ```
  At least one of `message_id` or `uid` is required.  When `uid` is
  provided, `source_folder` is also required.  When only `message_id`
  is provided the server searches all archive folders.

  Response (200):
  ```json
  {
    "status": "moved",
    "message_id": "<id>",
    "uid": 42,
    "source_folder": "<IMAP folder>",
    "target_subfolder": "<target>"
  }
  ```

  Errors: 400 (invalid/missing parameters, path escapes archive root),
  404 (message not found), 502 (IMAP error).

## Archive folder-delete (state-mutating, requires confirmation)

- **`POST /archive-delete`** — Delete an archive subfolder.  By default
  only empty folders (no messages, no child folders) can be deleted; use
  `force: true` to override.  Requires operator confirmation per the
  safety rules above.

  JSON request body:
  ```json
  {
    "source_folder": "<archive subfolder path>",
    "confirm": true,
    "force": false
  }
  ```

  Response (200):
  ```json
  {
    "status": "deleted",
    "source_folder": "<folder>"
  }
  ```

  Errors: 400 (missing `source_folder`, `confirm` not true, folder not
  empty without `force:true`, path escapes archive root), 404 (folder
  not found), 502 (IMAP error).

## Archive message-delete (state-mutating, requires confirmation)

- **`POST /archive-message-delete`** — Permanently delete a single
  archived message.  Requires `source_folder` and at least one of `uid`
  or `message_id`.  When `uid` is omitted the server resolves by
  Message-ID within `source_folder`; when `uid` is provided but stale,
  `message_id` (if supplied) is used as a fallback.  Marks the message
  `\Deleted` and `EXPUNGE`s — this is irreversible.  Requires operator
  confirmation per the safety rules above.

  JSON request body:
  ```json
  {
    "uid": 42,
    "source_folder": "<archive subfolder path>",
    "confirm": true,
    "message_id": "<Message-ID header (optional, may be used alone)>"
  }
  ```

  Response (200):
  ```json
  {
    "status": "deleted",
    "uid": 42,
    "source_folder": "<folder>"
  }
  ```

  Errors: 400 (missing `source_folder`, neither `uid` nor `message_id`
  supplied, `confirm` not true, path escapes archive root), 404 (uid
  not found in source_folder), 502 (IMAP error).

  The existing `POST /delete` endpoint (board messages by Message-ID)
  is unchanged — this endpoint fills the gap for archive-scoped
  uid+folder deletion.

## Archive folder-rename (state-mutating, requires confirmation)

- **`POST /archive-rename`** — Rename an archive subfolder in place.
  Performs an IMAP `RENAME` — O(1), preserves all contained messages.
  Requires operator confirmation per the safety rules above.

  JSON request body:
  ```json
  {
    "source_folder": "<current archive subfolder path>",
    "target_name": "<new folder name (last component)>",
    "confirm": true
  }
  ```
  Use `target_path` instead of `target_name` to reparent the folder
  (move it under a different parent).

  Response (200):
  ```json
  {
    "status": "renamed",
    "source_folder": "<old path>",
    "target": "<new path>"
  }
  ```

  Errors: 400 (missing parameters, `confirm` not true, path escapes
  archive root), 404 (source folder not found), 409 (target already
  exists — no silent merge), 502 (IMAP error).

## Push attachments to file-hub (state-mutating, requires confirmation)

- **`POST /email/<message_id>/attachments/to-file-hub`** — Upload one
  or all attachments of a mail message to robotsix-file-hub so
  downstream agents can work with the documents.  Requires operator
  confirmation per the safety rules above.

  The `<message_id>` is the RFC 2822 Message-ID (URL-encoded in the
  path).  The optional JSON body selects which attachment(s) to push:

  ```json
  {}
  ```
  Push all attachments (default when body is empty or omitted).

  ```json
  {"filename": "invoice.pdf"}
  ```
  Push a single attachment by filename.

  ```json
  {"index": 0}
  ```
  Push a single attachment by zero-based index.

  Response (200):
  ```json
  {
    "attachments": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "filename": "invoice.pdf",
        "size": 12345,
        "content_type": "application/pdf"
      }
    ]
  }
  ```

  Errors: 400 (message has no attachments, invalid body), 404
  (unknown message_id or attachment not found), 502 (IMAP or
  file-hub unreachable), 503 (file-hub not configured).

## Compose new draft (state-mutating, requires confirmation)

- **`POST /compose-draft`** — Create a NEW outgoing draft (not a reply)
  with optional file-hub attachments.  The draft is stored in the
  board's Draft-ready column so the operator can review and send it.
  Requires operator confirmation per the safety rules above.

  JSON request body:
  ```json
  {
    "account": "ROBOTSIX",
    "to": "recipient@example.com",
    "subject": "Subject line",
    "body": "Draft body text",
    "attachments": ["file-hub-id-1", "file-hub-id-2"]
  }
  ```

  `account`, `to`, `subject`, and `body` are required.  `attachments`
  is an optional list of file-hub file IDs; each is validated against
  the file-hub service before the draft is stored.

  Response (201):
  ```json
  {
    "message_id": "<compose-abc123@robotsix-auto-mail>",
    "account": "ROBOTSIX",
    "to": "recipient@example.com",
    "subject": "Subject line",
    "attachments": 2
  }
  ```

  Errors: 400 (missing/invalid fields), 404 (unknown account or
  unknown file-hub attachment id), 502 (file-hub unreachable),
  503 (file-hub not configured but attachments requested).

  **Note:** This endpoint only composes and stores the draft.  Sending
  is a separate, operator-confirmed action via `POST /send-draft`.
