# Security Policy

Thank you for helping keep `robotsix-auto-mail` and its users safe.

## Supported Versions

`robotsix-auto-mail` is pre-1.0 software (currently version `0.0.0`) and
has no tagged releases or stable release line yet. Security fixes are
applied only to the latest code on the `main` branch.

| Version | Supported          |
| ------- | ------------------ |
| `main`  | :white_check_mark: |

There are no numbered stable release lines to back-port fixes to yet;
once a stable release line exists this policy will be updated to describe
it.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security reports.**
Public disclosure before a fix is available puts users at risk.

Instead, report vulnerabilities privately:

1. **Preferred:** Use GitHub's private vulnerability reporting via the
   repository's **Security** tab → **Advisories** → **"Report a
   vulnerability"**. This opens a private Security Advisory that only the
   maintainer can see.
2. **Fallback:** If you cannot use GitHub Security Advisories, email the
   maintainer at **damien.robotsix@gmail.com**.

When reporting, please include enough detail to reproduce the issue: the
affected component, steps to reproduce, and the potential impact.

### Response and patch timeline

- **Acknowledgement:** We aim to acknowledge your report **within 72
  hours**.
- **Triage & fix:** Once a report is confirmed, a fix is targeted for
  release as soon as is practical after triage. This is a best-effort
  target, not a contractual SLA — `robotsix-auto-mail` is a pre-1.0,
  volunteer-maintained project, so timelines depend on severity and
  maintainer availability.

## Scope

In scope:

- Vulnerabilities in the `robotsix_auto_mail` package and other
  first-party code in this repository (CLI, ingestion pipeline, IMAP/SMTP
  clients, database layer, and the web board server).

Out of scope:

- Vulnerabilities in third-party dependencies — please report these to
  the relevant upstream project. If a dependency issue affects this
  project specifically, you may still let us know so we can bump or
  mitigate.
- Findings against unsupported, experimental, or not-yet-implemented
  code paths, and issues that require an already-compromised host or
  privileged local access.

## Acknowledgements

Reporters who follow responsible disclosure will be credited in the
relevant release notes or Security Advisory unless they request to remain
anonymous. Thank you for disclosing responsibly.
