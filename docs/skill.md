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

- Without `?account=`, the server uses a per-session cookie, then falls
  back to the default account.
- Use `?account=__all__` for the aggregate multi-account board view.

## GET endpoints

| Path | Response | Notes |
|------|----------|-------|
| `GET /` | 301 → `/board` | |
| `GET /board` | HTML | Full board UI |
| `GET /board-content` | JSON `{"columns_html":"…","triage_running":bool,"batch_op":{"op":…,"done":…,"total":…}\|null,"health":{…}\|null,"health_alerts_html":"…","unsubscribe_suggestions":{…}}` | Board payload (rendered columns + metadata); preferred for machine reads. `batch_op` is an object (verb + progress counts) while a batch op runs, else `null`; `health` carries the account-health watermark and `health_alerts_html` the rendered red-banner markup |
| `GET /health` | JSON `{"status":"ok"}` 200 | Liveness; returns 200 while the process is alive |
| `GET /probe-health` | JSON `{"accounts":{"<id>":{"status":"…","error":…}}}` | On-demand IMAP+SMTP connectivity probe across all accounts; persists each result to the account's `account_health` watermark |
| `GET /auth-status` | JSON `{"status":"…",…}` | Polls a running OAuth2 device-code flow; `status` is `idle`/`pending_prompt`/`pending_consent`/`success`/`error`. Cross-account: takes `?account_id=<id>` and ignores the session account |
| `GET /archive-folders` | JSON `{"delimiter":"/","folders":[…]}` | Available IMAP archive subfolders. Returns `{"delimiter":"/","folders":[]}` in aggregate mode |
| `GET /email/{message_id}/status` | plain text — triage action name | 404 if unknown |
| `GET /email/{message_id}` | HTML | Detail page; optional `?embed=1` strips chrome, `?draft=1` shows draft panel |
| `GET /archive-proposal/{message_id}` | JSON `{"subfolder":"…","archive_root":"…","folder_exists":bool,"overridden":bool,"source":"…"}` | Effective archive subfolder for the message. `overridden` is a bool (true when a user override is set); `source` is one of `override` / `llm` / `rule`. 404 if message_id unknown |
| `GET /static/{file}` | asset bytes | JS/CSS static files |

## POST endpoints

All POST endpoints accept `Content-Type: application/x-www-form-urlencoded`
(standard HTML form encoding).  Most return a `302` redirect to the
`redirect_to` field value (if supplied) or a hardcoded default.  Exception:
`/config-sync` returns JSON directly.

| Path | Form fields | Default redirect | Notes |
|------|------------|-----------------|-------|
| `POST /move` | `message_id`, `triage_action`, `redirect_to` (opt) | `/board` | Sets triage decision. Valid `triage_action` values (from `VALID_TRIAGE_ACTIONS`): **`INBOX`**, **`HUMAN_TRIAGE`**, **`PENDING_ACTION`**, **`TO_ARCHIVE`**, **`TO_DELETE`**, **`TO_CALENDAR`**, **`TO_ANSWER`**, **`DRAFT_READY`**. 400 on invalid. |
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
| `POST /save-draft` | `message_id`, `draft_text`, `redirect_to` (opt) | `/board` | Persists a draft reply text |
| `POST /send-draft` | `message_id`, `reply_mode`, `forward_to` (required when `reply_mode=forward`), `redirect_to` (opt) | `/board` | Sends the stored draft via SMTP. Valid `reply_mode` values: **`reply`**, **`reply_all`**, **`forward`**. `forward` additionally requires a `forward_to` recipient address. 400 on invalid |
| `POST /generate-draft` | `message_id`, `redirect_to` (opt) | `/board#<message_id>` | Triggers LLM draft generation in background |
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

### 6. Generate, inspect, and send a draft reply

```bash
# Step 1 – trigger generation (returns immediately; background LLM job)
curl -s -u <user>:<pass> -X POST \
  'https://deploy.robotsix.net/mail/generate-draft?account=main' \
  -d 'message_id=<id>'

# Step 2 – poll until status is not HUMAN_TRIAGE (draft ready)
curl -s -u <user>:<pass> \
  'https://deploy.robotsix.net/mail/email/<id>/status?account=main'

# Step 3 – send (reply_mode: "reply" or "reply_all")
curl -s -u <user>:<pass> -X POST \
  'https://deploy.robotsix.net/mail/send-draft?account=main' \
  -d 'message_id=<id>&reply_mode=reply'
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
