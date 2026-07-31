"""Tests for DetectionError and cross-registry smoke consistency checks."""

from __future__ import annotations

from robotsix_auto_mail.config.detect import (
    DetectionError,
)

# ---------------------------------------------------------------------------
# DetectionError
# ---------------------------------------------------------------------------


def test_detection_error_is_exception() -> None:
    """DetectionError is a proper Exception subclass."""
    err = DetectionError("test message")
    assert isinstance(err, Exception)
    assert str(err) == "test message"


def test_detection_error_chain() -> None:
    """DetectionError can chain from another exception."""
    cause = RuntimeError("original")
    err = DetectionError("wrapped")
    err.__cause__ = cause
    assert err.__cause__ is cause


# ---------------------------------------------------------------------------
# Smoke tests — unified registry consistency
# ---------------------------------------------------------------------------


def test_prompt_contains_all_providers() -> None:
    """Every prompt-table provider appears in the generated system prompt."""
    from robotsix_auto_mail.config.detect import (
        _DETECT_SYSTEM_PROMPT,
        _PROVIDER_DB,
    )

    for entry in _PROVIDER_DB:
        if entry.in_prompt_table and entry.imap_host:
            assert entry.label in _DETECT_SYSTEM_PROMPT, (
                f"Provider label '{entry.label}' missing from prompt"
            )
            assert entry.imap_host in _DETECT_SYSTEM_PROMPT, (
                f"Provider imap_host '{entry.imap_host}' missing from prompt"
            )


def test_mx_providers_covers_all_needles() -> None:
    """Every entry with mx_needles + imap_host has a matching MX entry."""
    from robotsix_auto_mail.config.detect import (
        _MX_PROVIDERS,
        _PROVIDER_DB,
        MailProvider,
    )

    for entry in _PROVIDER_DB:
        if not entry.mx_needles or not entry.imap_host:
            continue
        expected = MailProvider(
            imap_host=entry.imap_host,
            smtp_host=entry.smtp_host,
            imap_port=entry.imap_port,
            imap_tls_mode=entry.imap_tls_mode,
            smtp_port=entry.smtp_port,
            smtp_tls_mode=entry.smtp_tls_mode,
        )
        found = False
        for needles, provider in _MX_PROVIDERS:
            if provider is not None and needles == entry.mx_needles:
                assert provider == expected, (
                    f"MX MailProvider mismatch for {entry.label}: "
                    f"{provider} != {expected}"
                )
                found = True
                break
        assert found, (
            f"No _MX_PROVIDERS entry for {entry.label} with needles={entry.mx_needles}"
        )


def test_consistency_mx_vs_prompt() -> None:
    """Providers in both prompt table and MX must have identical hosts."""
    from robotsix_auto_mail.config.detect import (
        _DETECT_SYSTEM_PROMPT,
        _MX_PROVIDERS,
        _PROVIDER_DB,
    )

    for entry in _PROVIDER_DB:
        if not entry.in_prompt_table or not entry.imap_host:
            continue
        if not entry.mx_needles:
            continue
        # Find the MX entry for this provider
        mx_provider = None
        for needles, provider in _MX_PROVIDERS:
            if provider is not None and needles == entry.mx_needles:
                mx_provider = provider
                break
        if mx_provider is None:
            continue  # no MX entry — skip (e.g. Proton has no mx_needles)

        # The prompt should contain the same imap_host and smtp_host
        assert entry.imap_host in _DETECT_SYSTEM_PROMPT, (
            f"imap_host '{entry.imap_host}' for '{entry.label}' missing from prompt"
        )
        assert entry.smtp_host in _DETECT_SYSTEM_PROMPT, (
            f"smtp_host '{entry.smtp_host}' for '{entry.label}' missing from prompt"
        )
        # And the MX MailProvider must match the registry
        assert mx_provider.imap_host == entry.imap_host, (
            f"MX imap_host mismatch for {entry.label}: "
            f"{mx_provider.imap_host} != {entry.imap_host}"
        )
        assert mx_provider.smtp_host == entry.smtp_host, (
            f"MX smtp_host mismatch for {entry.label}: "
            f"{mx_provider.smtp_host} != {entry.smtp_host}"
        )
