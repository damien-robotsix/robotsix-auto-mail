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

### Board state

- **`GET /board-content`** → JSON `{"columns_html":"…","triage_running":bool,"batch_op":…,"health":…}`
  Returns the full board payload: rendered column HTML, triage-agent status,
  batch-operation progress (if any), and per-account health watermark.

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
  - Triggering triage or reconcile runs (`POST /run-triage`,
    `POST /reconcile`, `POST /force-triage-column`).
  - Saving or generating drafts (`POST /save-draft`, `POST /generate-draft`).
  - Config mutations (`PUT /config`, `POST /config/rollback`,
    `POST /config-sync`).

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
