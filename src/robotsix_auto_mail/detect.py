"""Email provider auto-detection via LLM.

Given an email address, ``detect_provider()`` calls an LLM to return the
correct IMAP and SMTP settings.  All ``pydantic_ai`` imports are lazy so
the rest of the CLI works without the optional ``[llm]`` dependency.
"""

from __future__ import annotations

import dataclasses
import os

import pydantic

from robotsix_auto_mail.config import MailConfig

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DetectionError(Exception):
    """Raised when provider detection fails for any reason."""


# ---------------------------------------------------------------------------
# Pydantic model — structured LLM output contract
# ---------------------------------------------------------------------------


class DetectedProvider(pydantic.BaseModel):
    """Structured output the LLM must return — validated by pydantic."""

    imap_host: str = pydantic.Field(..., min_length=1)
    imap_port: int = pydantic.Field(default=993, ge=1, le=65535)
    imap_tls_mode: str = pydantic.Field(default="direct-tls")
    smtp_host: str = pydantic.Field(..., min_length=1)
    smtp_port: int = pydantic.Field(default=587, ge=1, le=65535)
    smtp_tls_mode: str = pydantic.Field(default="starttls")

    @pydantic.field_validator("imap_tls_mode", "smtp_tls_mode")
    @classmethod
    def _validate_tls_mode(cls, v: str) -> str:
        if v not in {"starttls", "direct-tls", "none"}:
            raise ValueError(
                f"TLS mode must be one of starttls, direct-tls, none; got {v!r}"
            )
        return v


# ---------------------------------------------------------------------------
# Internal dataclass
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class MailProvider:
    """Lightweight, serialisable struct for detected mail parameters."""

    imap_host: str
    smtp_host: str
    imap_port: int = 993
    imap_tls_mode: str = "direct-tls"
    smtp_port: int = 587
    smtp_tls_mode: str = "starttls"


# ---------------------------------------------------------------------------
# System prompt — embeds all known provider data
# ---------------------------------------------------------------------------

_DETECT_SYSTEM_PROMPT = """\
You are an email provider configuration expert. Given an email address, \
return the correct IMAP and SMTP server settings as a JSON object.

**TLS mode rules:**
- `direct-tls`: TLS from the first byte — used on IMAP port 993 and SMTP \
port 465.
- `starttls`: plain connection upgraded to TLS via STARTTLS — used on \
IMAP port 143 and SMTP port 587.
- `none`: no TLS — for local/dev only.

**Known provider settings (use these exact values when the domain matches):**

| Provider | IMAP Host | IMAP Port | IMAP TLS | SMTP Host | SMTP Port | SMTP TLS |
|---|---|---|---|---|---|---|
| Gmail / Google Workspace | `imap.gmail.com` | 993 | `direct-tls` | `smtp.gmail.com` | 587 | `starttls` |
| Outlook / Hotmail / Live / MS365 | `outlook.office365.com` | 993 | `direct-tls` | `smtp.office365.com` | 587 | `starttls` |
| Yahoo Mail | `imap.mail.yahoo.com` | 993 | `direct-tls` | `smtp.mail.yahoo.com` | 587 | `starttls` |
| iCloud | `imap.mail.me.com` | 993 | `direct-tls` | `smtp.mail.me.com` | 587 | `starttls` |
| Fastmail | `imap.fastmail.com` | 993 | `direct-tls` | `smtp.fastmail.com` | 587 | `starttls` |
| Zoho Mail | `imap.zoho.com` | 993 | `direct-tls` | `smtp.zoho.com` | 587 | `starttls` |
| Proton Mail Bridge | `127.0.0.1` | 1143 | `none` | `127.0.0.1` | 1025 | `none` |
| GMX | `imap.gmx.com` | 993 | `direct-tls` | `mail.gmx.com` | 587 | `starttls` |
| mail.com | `imap.mail.com` | 993 | `direct-tls` | `smtp.mail.com` | 587 | `starttls` |
| Yandex Mail | `imap.yandex.com` | 993 | `direct-tls` | `smtp.yandex.com` | 587 | `starttls` |
| QQ Mail | `imap.qq.com` | 993 | `direct-tls` | `smtp.qq.com` | 587 | `starttls` |
| AOL Mail | `imap.aol.com` | 993 | `direct-tls` | `smtp.aol.com` | 587 | `starttls` |
| Mail.ru | `imap.mail.ru` | 993 | `direct-tls` | `smtp.mail.ru` | 587 | `starttls` |

**Domain heuristics (when the domain isn't in the table above):**
- `@gmail.com` or `@googlemail.com` → Gmail settings.
- `@outlook.com`, `@outlook.*`, `@hotmail.com`, `@hotmail.*`, \
`@live.com`, `@live.*`, `@msn.com` → Outlook/Microsoft 365 settings.
- `@yahoo.com`, `@yahoo.*`, `@ymail.com`, `@rocketmail.com` → Yahoo \
settings.
- `@icloud.com`, `@me.com`, `@mac.com` → iCloud settings.
- `@fastmail.com`, `@fastmail.*` → Fastmail settings.
- `@zoho.com`, `@zoho.*` → Zoho settings.
- `@proton.me`, `@protonmail.com`, `@pm.me` → Proton Mail Bridge (localhost).
- `@gmx.com`, `@gmx.*` → GMX settings.
- `@mail.com` → mail.com settings.
- `@yandex.com`, `@yandex.*` → Yandex settings.
- `@qq.com` → QQ Mail settings.
- `@aol.com` → AOL settings.
- `@mail.ru`, `@inbox.ru`, `@list.ru`, `@bk.ru` → Mail.ru settings.
- `@126.com`, `@163.com` → NetEase: `imap.126.com`/`imap.163.com` port \
993 `direct-tls`, `smtp.126.com`/`smtp.163.com` port 587 `starttls`.
- For self-hosted / custom domains (e.g. `@example.com`): the typical \
pattern is `imap.<domain>` port 993 and `smtp.<domain>` port 587 — but \
if you're unsure, return `imap.<domain>` / `smtp.<domain>` as best-guess \
with standard ports.

Return ONLY a JSON object matching the schema — no explanation, no markdown \
fences."""


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------


def detect_provider(
    email_address: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
) -> MailProvider:
    """Detect IMAP/SMTP settings for *email_address* via an LLM.

    Args:
        email_address: The email address to detect provider settings for.
        model: OpenRouter model name.  Defaults to the ``LLM_MODEL`` env
            var or ``"deepseek/deepseek-v4-flash"``.
        api_key: OpenRouter API key.  Defaults to the ``LLM_API_KEY`` env
            var.  Required unless the env var is set.

    Returns:
        A ``MailProvider`` with the detected settings.

    Raises:
        DetectionError: If the API key is missing, the LLM returns an
            invalid response, or any other error occurs.
    """
    # -- lazy imports so the rest of the CLI works without pydantic_ai --
    from pydantic_ai import Agent, PromptedOutput
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openrouter import OpenRouterProvider

    # -- resolve API key --
    resolved_key = api_key or os.environ.get("LLM_API_KEY", "")
    if not resolved_key:
        raise DetectionError(
            "LLM_API_KEY environment variable is required"
        )

    # -- resolve model --
    resolved_model = model or os.environ.get(
        "LLM_MODEL", "deepseek/deepseek-v4-flash"
    )

    # -- build agent --
    provider = OpenRouterProvider(api_key=resolved_key)
    agent_model = OpenAIChatModel(
        model_name=resolved_model,
        provider=provider,
    )
    agent = Agent(
        model=agent_model,
        output_type=PromptedOutput(DetectedProvider),
    )

    # -- call LLM --
    try:
        result = agent.run_sync(
            _DETECT_SYSTEM_PROMPT + "\n\n" + email_address
        )
    except Exception as exc:
        raise DetectionError(str(exc)) from exc

    # -- extract and convert --
    detected: DetectedProvider = result.data
    return MailProvider(
        imap_host=detected.imap_host,
        imap_port=detected.imap_port,
        imap_tls_mode=detected.imap_tls_mode,
        smtp_host=detected.smtp_host,
        smtp_port=detected.smtp_port,
        smtp_tls_mode=detected.smtp_tls_mode,
    )


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def provider_to_config(
    provider: MailProvider,
    username: str,
    db_path: str = "mail.db",
) -> MailConfig:
    """Convert a ``MailProvider`` + username into a ``MailConfig``.

    The password is always set to ``""`` — it is handled separately via
    ``secrets.yaml``.
    """
    return MailConfig(
        imap_host=provider.imap_host,
        imap_port=provider.imap_port,
        imap_tls_mode=provider.imap_tls_mode,
        smtp_host=provider.smtp_host,
        smtp_port=provider.smtp_port,
        smtp_tls_mode=provider.smtp_tls_mode,
        username=username,
        password="",
        db_path=db_path,
        imap_folder="INBOX",
    )


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------


def render_config(config: MailConfig, fmt: str = "yaml") -> str:
    """Render a ``MailConfig`` as a valid YAML or TOML config file.

    The ``auth.password`` field is always emitted as ``""`` with a
    trailing comment pointing to ``config/secrets.yaml``.
    """
    if fmt == "toml":
        return _render_config_toml(config)
    return _render_config_yaml(config)


def _render_config_yaml(config: MailConfig) -> str:
    """Render *config* as YAML."""
    return f"""\
# Auto-detected mail configuration for {config.username}
# Generated by: robotsix-auto-mail detect
#
# Verify these settings before using — run `robotsix-auto-mail probe`.

imap:
  host: {config.imap_host}
  port: {config.imap_port}
  tls_mode: {config.imap_tls_mode}
  folder: {config.imap_folder}

smtp:
  host: {config.smtp_host}
  port: {config.smtp_port}
  tls_mode: {config.smtp_tls_mode}

auth:
  username: {config.username}
  password: ""  # password stored in config/secrets.yaml

store:
  path: {config.db_path}
"""


def _render_config_toml(config: MailConfig) -> str:
    """Render *config* as TOML."""
    return f"""\
# Auto-detected mail configuration for {config.username}
# Generated by: robotsix-auto-mail detect
#
# Verify these settings before using — run `robotsix-auto-mail probe`.

[imap]
host = "{config.imap_host}"
port = {config.imap_port}
tls_mode = "{config.imap_tls_mode}"
folder = "{config.imap_folder}"

[smtp]
host = "{config.smtp_host}"
port = {config.smtp_port}
tls_mode = "{config.smtp_tls_mode}"

[auth]
username = "{config.username}"
password = ""  # password stored in config/secrets.yaml

[store]
path = "{config.db_path}"
"""


def render_secrets(password: str) -> str:
    """Render a ``secrets.yaml`` file from a password string.

    When *password* is empty, emits a fill-in comment.
    """
    if password:
        pw_line = f"{password!r}"
    else:
        pw_line = '""  # fill in your password'

    return f"""\
# Secrets for robotsix-auto-mail.
# This file is git-ignored — do not commit it.
mail_password: {pw_line}
"""
