# CODEX.md

# OpenCode Router — Codex instructions

## Overview

Local multi-API-key router with circuit breakers, telemetry, and a hardened control-panel dashboard.

## Key files

- `scripts/router.py` — Core routing logic (key rotation, circuit breakers, classify)
- `scripts/state.py` — Shared state helpers (load_json, save_json, atomic_write_text)
- `scripts/config.py` — Centralized `Config` dataclass
- `scripts/logging_setup.py` — Shared logging
- `dashboard/server.py` — HTTP API server (token auth, SSE, audit log)
- `dashboard/index.html` — HTML structure (references style.css + app.js)
- `dashboard/style.css` — all styles
- `dashboard/app.js` — frontend logic (api, tables, jobs, settings, exports, SSE)

## Running tests

```bash
python -m unittest discover -s tests -v
python -m py_compile scripts/*.py dashboard/server.py
```

## Router classifier

Only matches `500–504` as provider outages (`\b50[0-4]\b`), not arbitrary `5xx`-looking numbers like `line 512`.

## Dashboard auth

Every `/api/*` call requires `X-Router-Token` (constant-time compare via `hmac.compare_digest`).

## State files

- `state/events.jsonl` — telemetry events
- `state/audit.jsonl` — audit log
- `state/circuits.json` — circuit breaker state
- `state/key-pools.json` — key pool cooldowns/stats
