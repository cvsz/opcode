# CODEX.md

# OpenCode Router — Codex instructions

## Overview

Local multi-API-key router with circuit breakers, telemetry, and a hardened modular control-panel dashboard.

## Key files

- `scripts/router.py` — Core routing logic (key rotation, circuit breakers, classify, v2 API client, dual-path execution)
- `scripts/state.py` — Shared JSON state helpers
- `scripts/config.py` — Centralized `Config` dataclass
- `scripts/logging_setup.py` — Shared logging
- `scripts/db.py` — SQLite state store (events, audit, circuits, key pools, settings)
- `dashboard/server.py` — HTTP API server (token auth, SSE, audit log, rate limiting, pydantic schemas)
- `dashboard/index.html` — HTML structure (references style.css + app.js)
- `dashboard/style.css` — all styles (dark/light themes, responsive, ARIA)
- `dashboard/app.js` — frontend logic (api, tables, jobs, settings, exports, SSE)
- `Makefile` — All-in-one control: start/stop/status/restart, test/lint/type-check/security-scan/ci-all

## Running tests

```bash
python -m unittest discover -s tests -v
python -m py_compile scripts/*.py dashboard/server.py
make lint          # ruff check
make type-check    # mypy
make security-scan # bandit + pip-audit
make ci-all        # lint + type-check + security + tests
```

## Router classifier

Only matches `500–504` as provider outages (`\b50[0-4]\b`), not arbitrary `5xx`-looking numbers like `line 512`. Categories: `quota_exhausted`, `rate_limited`, `authentication`, `provider_outage`, `model_unavailable`, `execution_error`.

## Dashboard auth

Every `/api/*` call requires `X-Router-Token` (constant-time compare via `hmac.compare_digest`). Token rotates after 1000 requests or via `POST /api/token/regen`.

## State files

- `state/events.jsonl` — telemetry events
- `state/audit.jsonl` — audit log
- `state/circuits.json` — circuit breaker state
- `state/key-pools.json` — key pool cooldowns/stats
- `state/dashboard-settings.json` — dashboard settings
- `state/router.db` — SQLite dual-write

## Security notes

- `TEST_COMMAND` executed without `shell=True` — command injection neutralized
- `urlopen` restricted to `http://` / `https://` schemes only — SSRF neutralized
- Secrets never logged or printed; `last_line()` redacts secrets
