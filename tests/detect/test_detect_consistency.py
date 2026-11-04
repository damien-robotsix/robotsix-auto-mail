"""Cross-registry smoke consistency checks for the provider database."""

from __future__ import annotations


def test_mx_providers_covers_all_needles() -> None:
    """Every entry with mx_needles + imap_host has a matching MX entry."""
    from robotsix_auto_mail.detect import (
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
