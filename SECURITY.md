# SECURITY.md

# Security

## Dashboard

The control panel is a **local-only** tool. It binds to `127.0.0.1` by default and uses:

- Per-process random API token (`X-Router-Token`, constant-time compare)
- Host/Origin allow-list guards (DNS-rebinding + cross-origin protection)
- CSP with `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`
- Rate limiting (token bucket, 30 req/60s per IP) on mutating endpoints
- Audit log at `state/audit.jsonl` records action, IP, user agent for every mutating endpoint
- Token rotation after 1000 requests or via `POST /api/token/regen`; old tokens immediately invalidated

**Never** expose the dashboard directly to the internet. Use an authenticated reverse proxy if remote access is needed.

## Secrets

- API keys are never logged or printed
- `last_line()` redacts secrets before diagnostics output
- State files (`state/key-pools.json`) are written with `0600` permissions
- `auth export` prints secrets — the router deliberately never calls it

## Security fixes applied

- **Command injection (HIGH)**: `quality_gate.py` no longer uses `shell=True`. `TEST_COMMAND` is parsed with `shlex.split()` and executed as a list via `subprocess.run(cmd, shell=False)`. Regression test added.
- **SSRF (MEDIUM)**: All `urllib.request.urlopen` calls in `router.py` now validate the URL scheme (`http://` / `https://` only) via `_validate_url()`. `file://`, `ftp://`, `gopher://` etc. are rejected. Regression test added.
- **B105 false positives**: `None` values in `key_candidates()` marked with `# nosec` — they are Python `None`, not hardcoded passwords.

## Reporting

Report security issues privately to the project maintainer. Do not open public issues for security vulnerabilities.

## Scope

- Local control panel: in scope
- `opencode serve` v2 API: requires `OPENCODE_PASSWORD`; secure the transport
- Provider API keys: user responsibility

## Security scanning

```bash
make security-scan   # bandit + pip-audit
make ci-all          # lint + type-check + security + tests
```

Never suppress a security failure to make CI green.
