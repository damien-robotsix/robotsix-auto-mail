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
  (e.g. `INBOX`, `TO_ARCHIVE`, `TO_ANSWER`). Returns 404 for unknown IDs.

### Archive layout

- **`GET /archive-folders`** → JSON `{"delimiter":"/","folders":[…]}`.
  Lists available IMAP archive subfolders.

- **`GET /archive-proposal/<message_id>`** → JSON `{"subfolder":"…","archive_root":"…","folder_exists":bool,"overridden":bool,"source":"…"}`.
  Shows the effective archive subfolder for a message (source: `override`, `llm`, or `rule`).

- **`GET /archive/<folder>/messages`** → JSON `{"folder":"…","total":N,"shown":N,"messages":[…]}`.
  Lists messages inside an archive subfolder. Each message object contains `uid`, `subject`, `from`, `to`, `date`, `size`, and `flags`.  Accepts optional `?limit=N` (default 500, max 2000).  Returns 404 when the folder does not exist.

### Sent folder (outbound mail)

- **`GET /sent/messages?account=<id>`** → JSON `{"folder":"…","total":N,"shown":N,"messages":[…]}`.
  Lists messages in the account's **Sent** folder (newest first). Each message object contains `uid`, `subject`, `from`, `to`, `date`, `size`, and `flags` — the same shape as `/archive/<folder>/messages`.  The `?account=` query parameter is **required** — an unknown/mistyped account returns 404.  Accepts optional `?limit=N` (default 500, max 2000) and `?offset=N` (default 0) for paging.  Returns 404 when the server has no Sent folder.  Read-only — no side effects.

- **`GET /sent/message?account=<id>&uid=<n>`** → JSON `{"uid":N,"folder":"…","subject":"…","from":"…","to":[…],"cc":[…],"date":"…","body_plain":"…","body_html":"…","attachments":[{"filename":"…","mime_type":"…","size":N}]}`.
  Reads a single Sent message by UID (from `/sent/messages`) and enumerates its attachments.  The `?account=` and `?uid=` query parameters are **required** — an unknown account returns 404, a missing/non-integer `uid` returns 400.  Returns 404 when the UID no longer exists.  Read-only — no side effects.

### Liveness

- **`GET /health`** → JSON `{"status":"ok"}` 200. Liveness check only.

## Safety rules

- **Read operations** (all `GET` endpoints above) are always safe — they
  never modify state and may be called freely.

- **State-mutating operations** require **explicit user confirmation**
  before execution. These include:
  - Accepting or rejecting an archive proposal (`POST /move`,
    `POST /archive-proposal`).
  - Any batch operation (`POST /batch-delete`, `POST /batch-archive`,
    `POST /batch-archive-folder`).
  - Deleting or archiving individual messages (`POST /delete`,
    `POST /archive`).
  - Moving messages between archive folders (`POST /archive-move`).
  - Deleting archive folders (`POST /archive-delete`).
  - Deleting archived messages (`POST /archive-message-delete`).
  - Renaming archive folders (`POST /archive-rename`).
  - Triggering triage, reconcile, or force-fetch runs (`POST /run-triage`,
    `POST /reconcile`, `POST /force-fetch`, `POST /force-triage-column`).
  - Config mutations (`PUT /config`, `POST /config/rollback`,
    `POST /config-sync`).
  - Pushing email attachments to file-hub
    (`POST /email/<message_id>/attachments/to-file-hub`).
  - Composing a reply or new message into the mailbox Drafts folder
    (`POST /compose-draft`).

  Before executing any POST/PUT, confirm with the user and explain what
  the operation will do.

## Board mutations (state-mutating, requires confirmation)

These are the endpoints behind the board's own buttons — move a card
between columns, archive or delete one message, or run a column-wide
batch.  All require operator confirmation per the safety rules above.

Composing a reply or new message is **not** a board action: it writes a
genuine draft directly into the account's IMAP Drafts folder via
`POST /compose-draft` (see "Compose to mailbox Drafts" below).  The
board no longer stores drafts and no longer sends mail — the operator
reviews and sends manually from their own mail client (Gmail /
Roundcube).

**Conventions shared by every endpoint in this section:**

- **`account` is a QUERY PARAMETER, never a body field**:
  `POST /move?account=ROBOTSIX`.  An `account` key inside the JSON
  body is silently ignored and the request falls back to the default
  account (the first configured one, or the `account` cookie) — for a
  `message_id` that lives in another mailbox that surfaces as a
  misleading **404 Not found**.  Always pass `?account=<id>` when more
  than one account is configured (`GET /accounts` lists the ids).
- Body: `application/x-www-form-urlencoded` or `application/json` with
  the same field names.  `message_id` is the message's Message-ID header
  exactly as returned by the board/detail endpoints.
- Success is a **302 redirect** (to `/board`, or to the optional
  `redirect_to` body field when it is a safe relative path) with no JSON
  body — treat any 3xx as success.  Errors: **400** (missing/invalid
  field, malformed JSON body), **404** (unknown `message_id` for the
  selected account, or unknown `?account=`), **502** (IMAP/SMTP error).

- **`POST /move`** — Move a card to another column (sets the triage
  decision, source `user`).

  ```json
  {"message_id": "<Message-ID>", "triage_action": "TO_ARCHIVE"}
  ```
  `triage_action` must be one of `INBOX`, `HUMAN_TRIAGE`, `PENDING_ACTION`,
  `TO_ARCHIVE`, `TO_DELETE`, `TO_CALENDAR`, `TO_ANSWER`.  Moving to
  `TO_ARCHIVE` also (best-effort) proposes an archive subfolder; moving to
  `TO_CALENDAR` queues a calendar event.

- **`POST /archive`** — Archive one message to the card's **proposed**
  archive subfolder (the one shown on the To Archive card) and remove the
  card.

  ```json
  {"message_id": "<Message-ID>"}
  ```

- **`POST /delete`** — Permanently delete one message from the IMAP
  mailbox and the local DB.

  ```json
  {"message_id": "<Message-ID>"}
  ```

- **`POST /batch-archive`** and **`POST /batch-delete`** — Column-wide
  "Archive All" / "Delete All".  **No body fields**: they act on every
  card currently in the `TO_ARCHIVE` / `TO_DELETE` column of the selected
  account (`?account=`; in the aggregate `?account=__all__` view they fan
  out to every account).  The response is an immediate **302 to
  `/board`**; the work runs in a background worker and progress shows in
  the board's batch banner (poll `GET /board-content`).  Only one batch
  operation runs per account at a time — a second request while one is
  running, or a request on an empty column, is a no-op redirect.
  `POST /batch-archive-folder` with body `{"folder": "<subfolder>"}`
  archives only the To Archive cards whose proposed destination is that
  subfolder (empty = archive root).

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

## Compose to mailbox Drafts (state-mutating, requires confirmation)

- **`POST /compose-draft`** — Compose a **reply** or a **new** message
  and write it as a genuine RFC822 draft directly into the account's
  real IMAP Drafts folder (e.g. `[Gmail]/Drafts`).  The draft then
  appears natively in the operator's mail client (Gmail / Roundcube)
  for manual review and send.  The board stores nothing and never sends
  mail.  Requires operator confirmation per the safety rules above.

  JSON request body:
  ```json
  {
    "account": "ROBOTSIX",
    "to": "recipient@example.com",
    "subject": "Subject line",
    "body": "Message body text",
    "attachments": ["file-hub-id-1", "file-hub-id-2"],
    "reply_to_message_id": "<orig@example.com>",
    "reply_all": false
  }
  ```

  `account` and `body` are always required.  For a **new** message
  `to` and `subject` are required.  For a **reply** set
  `reply_to_message_id` to the original message's Message-ID: `to` and
  `subject` then default to the original sender and a `Re:` subject, the
  `In-Reply-To`/`References` threading headers are set automatically, and
  `reply_all: true` adds the original recipients as Cc.  `attachments`
  is an optional list of file-hub file IDs; **every** attachment is
  fetched from file-hub and included as a MIME part.  If any attachment
  cannot be fetched the request fails loudly (no stripped draft is ever
  written).

  Response (201):
  ```json
  {
    "account": "ROBOTSIX",
    "to": "recipient@example.com",
    "subject": "Subject line",
    "drafts_folder": "[Gmail]/Drafts",
    "attachments": 2,
    "reply": true
  }
  ```

  Errors: 400 (missing/invalid fields), 404 (unknown account, unknown
  reply target, or unknown file-hub attachment id), 502 (file-hub
  unreachable or IMAP APPEND failed), 503 (file-hub not configured but
  attachments requested).

  **Note:** This endpoint only writes the draft into the mailbox.
  Sending is done manually by the operator from their own mail client.

  **Auto-archive on send:** when a compose-draft is a **reply**
  (`reply_to_message_id` set), the board records a link between the draft
  and the original card.  Once the reply is actually sent — either from
  the board or externally from the operator's mail client — the next
  `POST /reconcile` cycle detects the sent message in the account's Sent
  folder (matched by its `In-Reply-To` header) and automatically moves the
  original card to the `TO_ARCHIVE` column, so a sent draft never lingers
  as a phantom card.  Reconciliation is idempotent and never archives a
  draft that was not actually sent.
