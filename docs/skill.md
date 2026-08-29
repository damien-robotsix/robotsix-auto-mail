# Board HTTP API

External agents can read board state and trigger all board actions directly
over HTTP — no agent-comm bridge needed.

## Base URLs and auth

### Gateway (production)

```
https://deploy.robotsix.net/mail
```

Fronted by the central-deploy gateway; requires **HTTP Basic Auth**.

### Direct (local / development)

```
http://<host>:<port>
```

The port is passed to `robotsix-auto-mail serve --port <N>`.  No auth at
this layer — the server listens on `0.0.0.0`.

## Account selection

Append `?account=<account_id>` (e.g. `?account=main`) to any request.

- Without `?account=`, the server uses a per-session cookie, then selects
  the first account in configured order.
- Use `?account=__all__` for the aggregate multi-account board view.

## GET endpoints

| Path | Response | Notes |
|------|----------|-------|
| `GET /` | 301 → `/board` | |
| `GET /board` | HTML | Full board UI |
| `GET /accounts` | JSON `{"accounts":[{"id":"…","address":"…","label":"…","healthy":true\|false\|null},…]}` | Lists every configured mail account with its id, email address, human-readable label, and health status. `healthy` is `true` when the last IMAP/SMTP connectivity probe succeeded, `false` when it failed, `null` when no probe has run. Read-only — no side effects |
| `GET /board-content` | JSON `{"columns_html":"…","triage_running":bool,"batch_op":{"op":…,"done":…,"total":…}\|null,"health":{…}\|null,"health_alerts_html":"…","unsubscribe_suggestions":{…}}` | Board payload (rendered columns + metadata); preferred for machine reads. `batch_op` is an object (verb + progress counts) while a batch op runs, else `null`; `health` carries the account-health watermark and `health_alerts_html` the rendered red-banner markup |
| `GET /board-content?format=json` | JSON `{"columns":{…},"triage_running":bool}` | Same board data but in structured form (no HTML). Each card has `message_id`, `subject`, `from`, `date`, `status` (triage column), and `account` (owning account id). Cards grouped by triage column. Optional `?account=<id>` filters to one account; unknown id returns 404. Omitted `?account=` returns all accounts |
| `GET /health` | JSON `{"status":"ok"}` 200 | Liveness; returns 200 while the process is alive |
| `GET /probe-health` | JSON `{"accounts":{"<id>":{"status":"…","error":…}}}` | On-demand IMAP+SMTP connectivity probe across all accounts; persists each result to the account's `account_health` watermark |
| `GET /settings-panel` | HTML | Settings page; mounts the shared `@robotsix/ui` config panel against the config surface below |
| `GET /config` | JSON `{"config":{…},"schema":{…},"version":N}` | Effective config with secrets masked, plus the JSON Schema the panel renders. Reachable without selecting an account |
| `PUT /config` | JSON `{"config":{…},"version":N}` | Partial update; omitted keys keep their value, a blank/masked secret keeps the stored one. `422` `problem+json` on validation failure |
| `GET /config/versions` | JSON `{"versions":[{"version":N,"timestamp":"…","changed_keys":[…]}]}` | Recent versions, newest first. Secret values are never stored |
| `POST /config/rollback` | JSON `{"config":{…},"version":N}` | Body `{"version": N}`; restores that version as a new one, keeping current secrets |
| `GET /auth-status` | JSON `{"status":"…",…}` | Polls a running OAuth2 device-code flow; `status` is `idle`/`pending_prompt`/`pending_consent`/`success`/`error`. Cross-account: takes `?account_id=<id>` and ignores the session account |
| `GET /archive-folders` | JSON `{"delimiter":"/","folders":[…]}` | Available IMAP archive subfolders. Returns `{"delimiter":"/","folders":[]}` in aggregate mode |
| `GET /archive/<folder>/messages` | JSON `{"folder":"…","full_path":"…","total":N,"shown":N,"messages":[…]}` | List messages in an archive subfolder. Each message object carries `uid`, `subject`, `from`, `to`, `date`, `size`, `flags`, and `message_id`. Optional `?limit=N` (default 500, max 2000). Returns `{"messages":[]}` in aggregate mode |
| `GET /sent/messages` | JSON `{"folder":"…","total":N,"shown":N,"messages":[…]}` | List messages in the Sent folder (newest first). Each message object carries `uid`, `subject`, `from`, `to`, `date`, `size`, `flags`, and `message_id`. Optional `?limit=N` (default 500, max 2000) and `?offset=N` (default 0). Returns `{"messages":[]}` in aggregate mode |
| `GET /email/{message_id}/status` | plain text — triage action name | 404 if unknown |
| `GET /email/{message_id}` | HTML | Detail page; optional `?embed=1` strips chrome |
| `GET /archive-proposal/{message_id}` | JSON `{"subfolder":"…","archive_root":"…","folder_exists":bool,"overridden":bool,"source":"…"}` | Effective archive subfolder for the message. `overridden` is a bool (true when a user override is set); `source` is one of `override` / `llm` / `rule`. 404 if message_id unknown |
| `GET /static/{file}` | asset bytes | JS/CSS static files |

## POST endpoints

All POST endpoints accept `Content-Type: application/x-www-form-urlencoded`
(standard HTML form encoding) **and** `Content-Type: application/json`.
When sending JSON, use the same field names as the form-encoded version
(see the table below).  Most return a `302` redirect to the `redirect_to`
field value (if supplied) or a hardcoded default.  Exception:
`/config-sync` returns JSON directly.

| Path | Form fields | Default redirect | Notes |
|------|------------|-----------------|-------|
| `POST /move` | `message_id`, `triage_action`, `redirect_to` (opt) | `/board` | Sets triage decision. Valid `triage_action` values (from `VALID_TRIAGE_ACTIONS`): **`INBOX`**, **`HUMAN_TRIAGE`**, **`PENDING_ACTION`**, **`TO_ARCHIVE`**, **`TO_DELETE`**, **`TO_CALENDAR`**, **`TO_ANSWER`**. 400 on invalid. |
| `POST /delete` | `message_id`, `redirect_to` (opt) | `/board` | IMAP deletion + DB row removal. 502 on IMAP error |
| `POST /archive` | `message_id`, `redirect_to` (opt) | `/board` | IMAP folder-move + DB row removal. 400/502 on error |
| `POST /save-notes` | `message_id`, `notes`, `redirect_to` (opt) | `/board` | Persists notes. `notes` is NOT stripped of whitespace |
| `POST /batch-delete` | *(none)* | `/board` | Fire-and-forget: deletes all `TO_DELETE` records in background. Single-flighted by watermark |
| `POST /batch-archive` | *(none)* | `/board` | Fire-and-forget: archives all `TO_ARCHIVE` records in background |
| `POST /batch-archive-folder` | `folder` | `/board` | Like `/batch-archive` scoped to one destination subfolder |
| `POST /config-sync` | *(none)* | — (returns JSON `ConfigSyncResult`, not redirect) | Triggers config-sync advisory. 503 on error |
| `POST /run-triage` | *(none)* | `/board` | Launches triage agent in background. Idempotent (no-op if already running) |
| `POST /reconcile` | *(none)* | `/board` | Launches reconcile in background |
| `POST /force-triage-column` | `action` | `/board` | Clears all triage decisions for `action` then re-runs triage. Same valid values as `triage_action`. 400 on invalid |
| `POST /archive-proposal` | `message_id`, `subfolder`, `redirect_to` (opt) | `/board` | Saves an archive-subfolder choice for a message |
| `POST /compose-draft` | JSON only: `account`, `body`, `to`/`subject` (new msg), `reply_to_message_id`+`reply_all` (reply), `attachments` (opt) | — (returns JSON `201`, not redirect) | Composes a reply or new message and `APPEND`s it as a genuine RFC822 draft into the account's IMAP Drafts folder, with all file-hub attachments and threading headers. The board stores no draft and sends no mail. Fails loudly (no stripped draft) if an attachment cannot be fetched. 400/404/502/503 on error |
| `POST /auth-start` | `account_id` | — (returns JSON flow state, not redirect) | Starts the OAuth2 device-code flow for a Microsoft account; blocks up to ~15s for the device prompt then returns the flow state JSON. Cross-account (ignores the session account). 400 for unknown / non-Microsoft accounts |

> **Redirect-following note**: curl follows redirects with `-L`. Without
> `-L`, a POST returns the 302 directly. An agent that only needs the
> side-effect (triage decision set, archive triggered, etc.) can ignore
> the redirect body.

## curl examples

All examples assume the gateway base URL. Replace `<user>:<pass>` with
gateway Basic Auth credentials and `<id>` with the target message ID.

### 1. Read board state as JSON

```bash
curl -s -u <user>:<pass> \
  'https://deploy.robotsix.net/mail/board-content?account=main'
```

### 2. Set a triage decision

```bash
curl -s -u <user>:<pass> -X POST \
  'https://deploy.robotsix.net/mail/move?account=main' \
  -d 'message_id=<id>&triage_action=TO_ARCHIVE'
```

### 3. Archive a message immediately (IMAP move + DB delete)

```bash
curl -s -u <user>:<pass> -X POST \
  'https://deploy.robotsix.net/mail/archive?account=main' \
  -d 'message_id=<id>'
```

### 4. Delete a message

```bash
curl -s -u <user>:<pass> -X POST \
  'https://deploy.robotsix.net/mail/delete?account=main' \
  -d 'message_id=<id>'
```

### 5. Run triage

```bash
curl -s -u <user>:<pass> -X POST \
  'https://deploy.robotsix.net/mail/run-triage?account=main'
```

### 6. Compose a reply into the mailbox Drafts folder

Composing writes a genuine draft into the account's real IMAP Drafts
folder; the operator reviews and **sends manually** from their own mail
client (Gmail / Roundcube).  The board never sends mail.

```bash
# Reply to an existing message (threading + Cc derived from the original).
# Attachments are file-hub IDs; every one is fetched and included as a
# MIME part, or the request fails loudly (no stripped draft is written).
curl -s -u <user>:<pass> -X POST \
  'https://deploy.robotsix.net/mail/compose-draft?account=main' \
  -H 'Content-Type: application/json' \
  -d '{"account":"main","reply_to_message_id":"<id>","reply_all":false,
       "body":"Thanks — see attached.","attachments":["<file-hub-id>"]}'

# A brand-new message just supplies to/subject/body instead of a reply id.
curl -s -u <user>:<pass> -X POST \
  'https://deploy.robotsix.net/mail/compose-draft?account=main' \
  -H 'Content-Type: application/json' \
  -d '{"account":"main","to":"someone@example.com",
       "subject":"Hello","body":"Message body."}'
```

### 7. Trigger reconcile

```bash
curl -s -u <user>:<pass> -X POST \
  'https://deploy.robotsix.net/mail/reconcile?account=main'
```

### 8. Check liveness

```bash
curl -s 'https://deploy.robotsix.net/mail/health'
```

### 9. Batch-archive all TO_ARCHIVE messages

```bash
curl -s -u <user>:<pass> -X POST \
  'https://deploy.robotsix.net/mail/batch-archive?account=main'
```

### 10. Config sync (returns JSON, not a redirect)

```bash
curl -s -u <user>:<pass> -X POST \
  'https://deploy.robotsix.net/mail/config-sync?account=main'
```

### 11. List configured accounts

```bash
curl -s -u <user>:<pass> \
  'https://deploy.robotsix.net/mail/accounts'
```

### 12. Structured board data (all accounts)

```bash
curl -s -u <user>:<pass> \
  'https://deploy.robotsix.net/mail/board-content?format=json'
```

### 13. Structured board data (single account)

```bash
curl -s -u <user>:<pass> \
  'https://deploy.robotsix.net/mail/board-content?format=json&account=main'
```
