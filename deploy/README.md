# Continuous deployment — server.robotsix.net

This directory holds everything needed to run `robotsix-auto-mail` on
`server.robotsix.net` as an auto-updating Docker stack reachable at
`https://mail.robotsix.net`.

How it fits together:

```
merge to main ─▶ release.yml builds & pushes ghcr.io/…/robotsix-auto-mail:main
                                              │
                            Watchtower polls ─┘ (every 5 min) ─▶ redeploys
                                                                  ingester + board
internet ─▶ nginx (TLS + basic auth) ─▶ 127.0.0.1:8080 ─▶ board container
```

- **Continuous deploy:** pushing to `main` publishes a moving `:main` image
  (`../.github/workflows/release.yml`). Watchtower on the server polls GHCR
  and redeploys the `ingester` and `board` containers automatically.
- **Ingress:** the board binds to `127.0.0.1:8080` only. The host's shared
  nginx terminates TLS and enforces HTTP basic auth for `mail.robotsix.net`,
  then proxies to it (`nginx/mail.robotsix.net.conf`).

Versioned `v*` tags still publish semver + `latest` images; pin `IMAGE_TAG`
in `.env` to a version to freeze deploys instead of tracking `main`.

---

## One-time server setup

Run these on `server.robotsix.net`.

### 1. Place this stack on the host

```sh
sudo mkdir -p /opt/robotsix-auto-mail
# Copy this deploy/ directory there (git clone, scp, or rsync), e.g.:
git clone https://github.com/damien-robotsix/robotsix-auto-mail.git /tmp/ram
cp -r /tmp/ram/deploy/* /opt/robotsix-auto-mail/
cd /opt/robotsix-auto-mail
```

### 2. Environment file

```sh
cp .env.example .env
$EDITOR .env
```

**Set `LLM_API_KEY` in `.env`** even if you also put `llm.api_key` in the
config file. The compose file passes `LLM_API_KEY` into the container, and an
empty value overrides the config key field — so leaving it blank disables the
LLM. If you prefer to keep the key only in `config/mail.local.yaml`, copy it
into `.env` as well so the two agree.

### 3. Mail configuration

The stack bind-mounts `./config`, `./data`, and `./logs` (created on first
run). Provide a config file with your IMAP/SMTP credentials:

```sh
mkdir -p config
# Option A — copy the example from the repo and edit:
cp /tmp/ram/config/mail.local.example.yaml config/mail.local.yaml
$EDITOR config/mail.local.yaml

# Option B — auto-detect from your address (needs LLM_API_KEY in .env):
docker compose run --rm ingester detect damien.robotsix@gmail.com
```

### 3a. Fix bind-mount ownership

The container runs as UID 1000 (`mailbot`), but files you create on the host
are owned by your login user. Give UID 1000 ownership of the config file and
the data/log directories, or the container cannot read its config (the
entrypoint reports `Config file not found`) or write the database:

```sh
sudo chown 1000:1000 config/mail.local.yaml
sudo chown -R 1000:1000 data logs
chmod 600 config/mail.local.yaml      # contains credentials
```

Verify connectivity before starting the daemons:

```sh
docker compose run --rm ingester probe
```

### 4. GHCR pull access (only if the package is private)

The simplest setup is to make the GHCR package **public** (GitHub → the
package → Package settings → Change visibility → Public). Then no auth is
needed and Watchtower pulls freely.

If you keep it private, give the host a token with `read:packages` and
uncomment the `config.json` volume in `docker-compose.yml`:

```sh
echo "$GHCR_TOKEN" | docker login ghcr.io -u damien-robotsix --password-stdin
# then uncomment:  - /root/.docker/config.json:/config.json:ro
```

### 5. Start the stack

```sh
docker compose up -d
docker compose ps          # ingester + board + watchtower should be Up
docker compose logs -f board
```

The board is now on `127.0.0.1:8080`. It is **not** yet reachable from the
internet — that's the next step.

---

## nginx + TLS + basic auth

The board has no authentication, so the proxy must enforce it. This uses the
certbot `--nginx` installer to manage TLS (the same pattern as the host's
other vhosts). DNS for `mail.robotsix.net` must already point at the server.

### 1. Basic-auth credentials

```sh
sudo mkdir -p /etc/nginx/htpasswd
sudo htpasswd -c /etc/nginx/htpasswd/mail.robotsix.net damien
# add more users without -c:  sudo htpasswd /etc/nginx/htpasswd/mail.robotsix.net someone
```

### 2. Install the bootstrap vhost (HTTP only, no auth yet)

`nginx/mail.robotsix.net.conf` is an HTTP-only reverse proxy. Install it first
so certbot can solve the ACME challenge over port 80 — do **not** add basic
auth yet, or the challenge can be blocked.

```sh
sudo cp nginx/mail.robotsix.net.conf /etc/nginx/sites-available/mail.robotsix.net
sudo ln -sf ../sites-available/mail.robotsix.net /etc/nginx/sites-enabled/mail.robotsix.net
sudo nginx -t && sudo systemctl reload nginx
```

### 3. Obtain the certificate (certbot injects TLS + the HTTPS redirect)

```sh
sudo certbot --nginx -d mail.robotsix.net --non-interactive --redirect
```

certbot rewrites the vhost: the original block becomes the `listen 443 ssl`
server, and a new port-80 block 301-redirects to HTTPS.

### 4. Add basic auth to the HTTPS block

Add it to the 443 block only — leaving port 80 (now a pure redirect) auth-free
so future certbot renewals are never blocked:

```sh
sudo python3 - <<'PY'
p = "/etc/nginx/sites-available/mail.robotsix.net"
s = open(p).read()
if "auth_basic" not in s:
    s = s.replace(
        "    location / {\n",
        '    auth_basic           "robotsix-auto-mail";\n'
        "    auth_basic_user_file /etc/nginx/htpasswd/mail.robotsix.net;\n\n"
        "    location / {\n",
        1,
    )
    open(p, "w").write(s)
PY
sudo nginx -t && sudo systemctl reload nginx
```

### 5. Verify

```sh
curl -s -o /dev/null -w "%{http_code}\n" https://mail.robotsix.net/board          # 401
curl -s -o /dev/null -w "%{http_code}\n" -u user:pass https://mail.robotsix.net/board  # 200
```

Then browse to `https://mail.robotsix.net/board` and authenticate.

---

## Day-2 operations

| Task | Command |
|---|---|
| Watch deploy activity | `docker compose logs -f watchtower` |
| Force an immediate update | `docker compose pull && docker compose up -d` |
| Freeze to a version | set `IMAGE_TAG=v1.2.3` in `.env`, then `docker compose up -d` |
| One-shot CLI command | `docker compose run --rm ingester <cmd>` (probe, triage, …) |
| Restart the board only | `docker compose restart board` |
| Stop everything | `docker compose down` |

Notes:

- **Single-writer SQLite:** the database under `./data` is shared by the
  ingester and the board. The board only reads it for display; the ingester
  is the sole writer, so there is no concurrent-writer contention.
- **Volume ownership:** containers run as UID 1000 (`mailbot`); the host
  `./data` and `./logs` directories end up owned by UID 1000.
- **Watchtower scope:** `WATCHTOWER_LABEL_ENABLE=true` means it only touches
  the two labeled services here, never other stacks on the host.
