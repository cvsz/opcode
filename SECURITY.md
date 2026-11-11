# SECURITY.md

# Security

## Dashboard

The control panel is a **local-only** tool. It binds to `127.0.0.1` by default and uses:

- Per-process random API token (`X-Router-Token`, constant-time compare)
- Host/Origin allow-list guards (DNS-rebinding + cross-origin protection)
- CSP with `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`
- No credentials stored or exposed; telemetry only

**Never** expose the dashboard directly to the internet. Use an authenticated reverse proxy if remote access is needed.

## Secrets

- API keys are never logged or printed
- `last_line()` redacts secrets before diagnostics output
- State files (`state/key-pools.json`) are written with `0600` permissions
- `auth export` prints secrets — the router deliberately never calls it

## Reporting

Report security issues privately to the project maintainer. Do not open public issues for security vulnerabilities.

## Scope

- Local control panel: in scope
- `opencode serve` v2 API: requires `OPENCODE_PASSWORD`; secure the transport
- Provider API keys: user responsibility
