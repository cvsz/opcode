# SECURITY.md

# Security

## Dashboard

The control panel is a **local-only** tool. It binds to `127.0.0.1` by default and uses:

- Per-process random API token (`X-Router-Token`, constant-time compare)
- Host/Origin allow-list guards (DNS-rebinding + cross-origin protection)
- CSP with `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`
- Rate limiting (token bucket, 30 req/60s per IP) on mutating endpoints
- Pydantic request schemas bounding prompt/task/model/limit values before job launch
- Audit log at `state/audit.jsonl` records action, IP, user agent, and **correlation ID** (`request_id`) for every mutating endpoint
- Token rotation after 1000 requests or via `POST /api/token/regen`; old tokens immediately invalidated
- **Log rotation** via `RotatingFileHandler` (configurable max bytes, backup count); logs include correlation IDs for request tracing

**Never** expose the dashboard directly to the internet. Use an authenticated reverse proxy if remote access is needed.

## Secrets

- API keys are never logged or printed
- `last_line()` redacts secrets before diagnostics output
- State files (`state/key-pools.json`) are written with `0600` permissions
- `auth export` prints secrets — the router deliberately never calls it

## Security fixes applied

- **Command injection (HIGH)**: `quality_gate.py` no longer uses `shell=True`. `TEST_COMMAND` is parsed with `shlex.split()` and executed as a list via `subprocess.run(cmd, shell=False)`. Regression test added.
- **SSRF (MEDIUM)**: All `urllib.request.urlopen` calls in `router.py` now validate the URL scheme (`http://` / `https://` only) via `_validate_url()`. `file://`, `ftp://`, `gopher://` etc. are rejected. Regression test added.
- **Request schema validation (NEW)**: `RunRequest` Pydantic model bounds every field — prompt ≤1,000,000 chars, task ≤50 chars, model ≤200 chars, `max_models` 0–100, `timeout` 0–3600 — so oversized or out-of-range payloads are rejected with 400 before reaching the shell. `scripts/security_fuzzer.py` verifies the server answers 4xx and never crashes on adversarial payloads.
- **SQLite concurrency (NEW)**: `scripts/db.py` opens a per-statement connection with `journal_mode=WAL`, `synchronous=NORMAL`, and a 30 s busy timeout; 10 threads × 50 writes verified lossless in `tests/test_sqlite_concurrency.py`.
- **Request correlation IDs (NEW)**: `X-Request-ID` header (auto-generated if missing) propagated through dashboard audit log, SQLite events, and router subprocess via `ROUTER_REQUEST_ID` env var. Enables end-to-end request tracing.
- **Log rotation (NEW)**: `scripts/logging_setup.py` provides `RotatingFileHandler` support; dashboard configurable via `--log-file`, `--log-max-bytes`, `--log-backup-count` CLI args.
- **B105 false positives**: `None` values in `key_candidates()` — they are Python `None`, not hardcoded passwords. Suppressed with a blanket `# nosec` (not B105-specific): sibling string keys share the dict's line range, so a B105-specific suppression triggers bandit's "nosec encountered (B105), but no failed test" warnings on those, and bandit's nosec parser rejects any free text after the id.

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
