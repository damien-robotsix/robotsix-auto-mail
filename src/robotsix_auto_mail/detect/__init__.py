"""Email provider auto-detection.

Two complementary detectors return IMAP/SMTP settings for an email address:

* :func:`autoconfig_lookup` — queries the Mozilla ISPDB and the domain's own
  autoconfig endpoint over HTTPS (no LLM, very accurate for known providers
  and many custom domains). Uses only the standard library.
* :func:`detect_provider` — asks an LLM, optionally with feedback describing a
  previous failed attempt so it can refine a non-obvious guess.

The implementation is split across internal submodules:

- ``models`` — ``DetectionError``, ``DetectedProvider``, ``MailProvider``,
  ``ProviderEntry``.
- ``detector`` — all detection logic: ``detect_provider``, ``autoconfig_lookup``,
  ``mx_lookup``, ``provider_from_mx``, ``is_microsoft_provider``,
  ``provider_to_config``, plus the provider database and prompt builders.

This module re-exports the public and previously-importable symbols so
``from robotsix_auto_mail.detect import ...`` keeps working unchanged.
"""

from __future__ import annotations

from robotsix_llmio.core import get_provider as get_provider

from robotsix_auto_mail.detect.detector import (
    _DETECT_SYSTEM_PROMPT as _DETECT_SYSTEM_PROMPT,
)
from robotsix_auto_mail.detect.detector import (
    _MX_PROVIDERS as _MX_PROVIDERS,
)
from robotsix_auto_mail.detect.detector import (
    _PROVIDER_DB as _PROVIDER_DB,
)
from robotsix_auto_mail.detect.detector import (
    autoconfig_lookup as autoconfig_lookup,
)
from robotsix_auto_mail.detect.detector import (
    detect_provider as detect_provider,
)
from robotsix_auto_mail.detect.detector import (
    is_microsoft_provider as is_microsoft_provider,
)
from robotsix_auto_mail.detect.detector import (
    mx_lookup as mx_lookup,
)
from robotsix_auto_mail.detect.detector import (
    provider_from_mx as provider_from_mx,
)
from robotsix_auto_mail.detect.detector import (
    provider_to_config as provider_to_config,
)
from robotsix_auto_mail.detect.models import (
    DetectedProvider as DetectedProvider,
)
from robotsix_auto_mail.detect.models import (
    DetectionError as DetectionError,
)
from robotsix_auto_mail.detect.models import (
    MailProvider as MailProvider,
)
from robotsix_auto_mail.detect.models import (
    ProviderEntry as ProviderEntry,
)

__all__ = [
    "_DETECT_SYSTEM_PROMPT",
    "_MX_PROVIDERS",
    "_PROVIDER_DB",
    "DetectedProvider",
    "DetectionError",
    "MailProvider",
    "ProviderEntry",
    "autoconfig_lookup",
    "detect_provider",
    "get_provider",
    "is_microsoft_provider",
    "mx_lookup",
    "provider_from_mx",
    "provider_to_config",
]
