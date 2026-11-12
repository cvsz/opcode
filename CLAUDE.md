# CLAUDE.md

# OpenCode Router — Claude Code instructions

This is a local multi-API-key router with circuit breakers, telemetry, and a hardened modular control-panel dashboard.

## Repo layout

- `dashboard/server.py` — Python HTTP server: API + SSE stream + audit log + token auth + pydantic schemas + rate limiting
- `dashboard/index.html` — HTML structure (references style.css + app.js)
- `dashboard/style.css` — all styles (dark/light themes, responsive, ARIA)
- `dashboard/app.js` — frontend logic (api, tables, jobs, settings, exports, SSE, confirm dialog)
- `scripts/router.py` — Core routing: key rotation, circuit breakers, classify, v2 API client (dual-path)
- `scripts/state.py` — Shared JSON state helpers
- `scripts/config.py` — Centralized `Config` dataclass
- `scripts/logging_setup.py` — Shared logging
- `scripts/db.py` — SQLite state store (events, audit, circuits, key pools, settings)
- `scripts/key_pool.py`, `optimizer.py`, `orchestrator.py`, `quality_gate.py` — Supporting CLIs
- `config/*.txt` — Per-task ordered model lists
- `state/` — Runtime state (JSONL + JSON + SQLite)
- `tests/` — unittest suite (67 tests)

## Common commands

```bash
python dashboard/server.py                    # start control panel (http://127.0.0.1:8765)
python -m unittest discover -s tests -v       # run tests (67 tests)
python -m py_compile scripts/*.py dashboard/server.py
shellcheck scripts/load-keys.sh
python scripts/router.py route --task code --prompt "..."
python scripts/router.py plan --task code
python scripts/router.py check
```

## Key design points

- Dashboard requires `X-Router-Token` on every `/api/*` call (constant-time compare via `hmac.compare_digest`)
- Token rotates after 1000 requests or via `POST /api/token/regen`; old tokens immediately invalidated
- SSE `/api/stream` broadcasts state changes; `EventSource` with auto-reconnect, polling fallback when tab hidden
- Rate limiting (token bucket, 30 req/60s per IP) on mutating endpoints
- Audit log at `state/audit.jsonl`; `GET /api/audit` returns last 500 entries (action, IP, user agent)
- Router classifier matches `500–504` only (not arbitrary `5xx`-looking numbers)
- Secrets never logged or printed; `last_line()` redacts secrets
- Pydantic request/response schemas with manual fallback for CI (no pydantic install required)
- SQLite state (dual-write with JSONL for backward compatibility)

## When making changes

1. Run `python -m py_compile scripts/*.py dashboard/server.py`
2. Run `python -m unittest discover -s tests -v`
3. Update `CHANGELOG.md` for user-visible changes
4. Keep dashboard modular: index.html (structure), style.css (styles), app.js (logic)
