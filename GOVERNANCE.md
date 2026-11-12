# GOVERNANCE.md

# Governance

## Decision-making

Changes to the router core (`scripts/router.py`), dashboard (`dashboard/`), or CI (`.github/workflows/`) require review before merge.

## Versioning

Follows semantic versioning. Breaking changes to the dashboard API (`/api/*`) or router CLI flags are noted in `CHANGELOG.md`.

## Security

Security issues: see `SECURITY.md`. The dashboard is a local control panel only — never expose it to the internet without an authenticated reverse proxy.

## Contributing

See `CONTRIBUTING.md`. All changes must pass `python -m unittest discover -s tests -v`, `python -m py_compile scripts/*.py dashboard/server.py`, `make lint`, `make type-check`, and `make security-scan`.

## Deprecation

The `classify()` text-based heuristic is deprecated in favor of structured `opencode serve` NDJSON events (see MASTER.md item 1).

## Security review process

All security-relevant changes (auth, authz, input validation, crypto, network) require review by a security reviewer before merge. Security findings must be addressed — not suppressed.
