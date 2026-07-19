Replace detect-secrets (Yelp, unmaintained since May 2024) with
  gitleaks (v8.30.1, actively maintained) for secret scanning:
  gitleaks-docker pre-commit hook, .gitleaks.toml config, and a new
  secret-scanning CI workflow with SARIF upload to GitHub Code Scanning.
