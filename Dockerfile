ARG BASE_DIGEST=sha256:c845af9399020c7e562969a13689e929074a10fd057acd1b1fad06a2fb068e97

# ---------------------------------------------------------------------------
# Builder stage — builds the wheel and installs the package
# ---------------------------------------------------------------------------
FROM python:3.14-slim@${BASE_DIGEST} AS builder

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /build

# git is required at build time: the only non-PyPI dep
# (robotsix-llmio) is a git source in [tool.uv.sources], so uv
# clones it during install. The slim base image has no git.
# DL3008: git apt version is intentionally unpinned — the exact Debian
# version shifts with every base-image apt index refresh, so pinning it
# would break reproducible builds on each upstream update.
# hadolint ignore=DL3008
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Bring in the `uv` binary so the install step can honour
# [tool.uv.sources] in pyproject.toml — pip cannot, and the
# only non-PyPI dep (robotsix-llmio) is declared there.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/

# uv.lock is the committed single source of truth for resolved git
# revs; it MUST be in the build context so the export step below reads
# the pinned commits instead of re-resolving `@main` at build time.
COPY pyproject.toml uv.lock ./
COPY src/ src/

# Install the EXACT revisions pinned in uv.lock (no fresh resolution),
# so two builds with no repo change install identical git revs — closing
# the unpinned-`@main` drift. Plain `uv pip install ".[llm]"` re-resolves
# and does NOT read uv.lock, so it cannot be used here.
#   - `uv export --frozen` reads uv.lock as-is and emits pinned git URLs
#     (`...?rev=main#<sha>`); --no-emit-project drops the local project so
#     it is not installed via the requirements file; --no-hashes avoids the
#     hash/VCS conflict (uv cannot hash a git checkout); --extra llm and
#     --extra microsoft select both the `.[llm]` and `.[microsoft]`
#     extras (the latter pulls in msal for Microsoft OAuth2).
#   - the project itself is then installed with --no-deps so its deps are
#     NOT re-resolved.
# --system installs into the image's system Python (the same
# /usr/local/lib/python3.14/site-packages/ path the production
# stage copies from), matching the previous `pip install` layout.
RUN uv export --frozen --no-emit-project --no-hashes --extra llm --extra microsoft -o /tmp/requirements.txt && \
    uv pip install --system --no-cache-dir -r /tmp/requirements.txt && \
    uv pip install --system --no-cache-dir --no-deps .

# ---------------------------------------------------------------------------
# Production stage — minimal runtime image with only the installed artifacts
# ---------------------------------------------------------------------------
FROM python:3.14-slim@${BASE_DIGEST} AS production

COPY --from=builder /usr/local/lib/python3.14/site-packages/ /usr/local/lib/python3.14/site-packages/
COPY --from=builder /usr/local/bin/robotsix-auto-mail /usr/local/bin/robotsix-auto-mail

RUN groupadd --gid 1000 mailbot && \
    useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash mailbot && \
    mkdir -p /home/mailbot/.data /home/mailbot/config /home/mailbot/.mail_log && \
    chown mailbot:mailbot /home/mailbot/.data /home/mailbot/config /home/mailbot/.mail_log

COPY --chown=mailbot:mailbot entrypoint.sh /usr/local/bin/entrypoint.sh

USER mailbot

# Run from the home directory so relative defaults resolve under it:
# the config file (config/mail.local.yaml) and the SQLite store
# (.data/mail.db) both land in the bind-mounted / persisted locations.
WORKDIR /home/mailbot

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
