# Troubleshooting

This is the error-oriented companion to [docs/connecting.md](connecting.md)
(configuration keys and the TLS-modes table) and the deployment FAQ in
[docs/deployment.md](deployment.md) (Docker, volumes, entrypoint).  It focuses
on connection, TLS, and authentication diagnosis and does **not** duplicate
the deployment FAQ entries.

## First diagnostic step: `probe`

When anything connection-related goes wrong, run:

```sh
robotsix-auto-mail probe
```

`probe` connects to IMAP and SMTP read-only — it reads and sends no mail.  It
prints the IMAP greeting, capability list, and folder listing, then the SMTP
EHLO response and ESMTP feature set.  It exits `0` when both sides succeed and
`1` if either side fails.  The targeted error it prints tells you which side
failed and why; map that to the table below.  (See
[docs/connecting.md](connecting.md#the-probe-command) for sample output.)

## IMAP / SMTP error map

The IMAP and SMTP clients raise parallel exception hierarchies.  Each row is
what the error means and how to fix it.

| IMAP | SMTP | Meaning | Fix |
|---|---|---|---|
| `ImapConnectionError` | `SmtpConnectionError` | Socket unreachable, wrong host/port, or no server greeting. | Check `imap_host`/`imap_port` (`smtp_host`/`smtp_port`) and that the host running the tool can reach the server on that port. |
| `ImapTlsError` | `SmtpTlsError` | STARTTLS negotiation or SSL handshake failure. | The TLS mode is wrong for the port: reconcile `imap_tls_mode`/`smtp_tls_mode` (`direct-tls` vs `starttls` vs `none`) with the port — see below. |
| `ImapAuthError` | `SmtpAuthError` | Bad credentials. | Check the username/password.  Providers like Gmail require an app password rather than the account password. |
| — | `SmtpSendError` | The server rejected the message on send. | Check the recipient/sender addresses and any provider send policy. |

## TLS mode guidance

There are three valid TLS modes, and the typical port pairings are:

| Protocol | `direct-tls` | `starttls` |
|---|---|---|
| IMAP | 993 | 143 |
| SMTP | 465 | 587 |

`none` disables TLS entirely and is insecure — local development only.  A
`*TlsError` almost always means the mode and port disagree (e.g. `direct-tls`
against a STARTTLS-only port).  The canonical config keys for these modes are
in the TLS-modes table in [docs/connecting.md](connecting.md#configuration-keys).

## Provider auto-detection issues

If `robotsix-auto-mail detect <email>` cannot resolve settings, it has worked
through its ladder — published autoconfig (Mozilla ISPDB / domain
`autoconfig`), then MX-record lookup, then an LLM fallback (see
[docs/connecting.md](connecting.md#auto-detection-with-detect)).  When all
three miss (or the LLM returns wrong settings, which is why `detect` verifies
by connecting afterward), fall back to writing `config/mail.local.yaml`
manually — the hand-edited approach is fully supported.  After editing, re-run
`robotsix-auto-mail probe` to confirm.

## Database / watermark issues

If `ingest` reports `database is locked`, two ingests are contending for the
same SQLite database — run them sequentially (the deployment FAQ shows a
`flock` cron wrapper).  Stale-watermark symptoms (mail seemingly not
re-fetched) are expected: ingestion is idempotent, deduplicating on
`Message-ID` and advancing the `imap_uid` watermark, so re-running an ingest
is always safe and will not duplicate stored mail.  See
[docs/ingestion.md](ingestion.md) for the schema and idempotency model.

## Board operation — stale UID errors (409)

When you use the board's **Move**, **Delete**, or **Archive** operations, or
trigger a **Batch Delete**, the system communicates with your IMAP server to
move or delete the actual message. The system tracks each message's IMAP UID
(unique identifier within a folder) in its local database.

**What is a stale UID?**  A stale UID occurs when the message no longer exists
in the IMAP folder where the system expects it. This typically happens when:

- You (or another client) manually moved the message to a different folder
  (e.g., moved the entire INBOX into `INBOX.Archive` or `INBOX.Archives`)
- The IMAP server purged the message
- The message was deleted via another mail client or web interface

**Error behavior:**  When the board detects a stale UID, it **stops and
returns a 409 Conflict error** instead of silently deleting the local record.
The error message clearly states:

```
Message {message-id} is no longer in INBOX — the tracked UID is stale,
so it was not {deleted|archived} and the board record was kept.
```

This prevents silent data loss: the local board record remains intact and you
can investigate the actual message on the server.

**How to recover:**

1. **Verify the message location** on your IMAP server using a mail client
   (Thunderbird, Apple Mail, Gmail web interface, etc.). Search for the message
   by sender or subject.
2. **If the message is in a different folder**, manually move it back to INBOX
   (or the folder configured in `imap_folder`) if you still want to delete or
   archive it via the board.
3. **If the message is gone entirely**, you can safely **delete the board record
   manually** via the database or wait for the triage agent to re-classify it
   (if applicable).
4. **Re-attempt the operation** (Move/Delete/Archive) once the message is in the
   expected location.

**Why we do this:**  IMAP `STORE` and `COPY` commands that target a non-existent
UID return success (RFC 3501) even though they affect zero messages. Without
explicit checking, such silent no-ops would cause the board to delete its local
record while leaving the actual message on the server — a form of data loss. The
409 response ensures you always know when something unexpected happened.
