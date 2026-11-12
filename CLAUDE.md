# CLAUDE.md

# OpenCode Router — Claude Code instructions

This is a local multi-API-key router with circuit breakers, telemetry, and a hardened control-panel dashboard.

## Repo layout

- `dashboard/index.html` — HTML structure (references style.css + app.js)
- `dashboard/style.css` — all styles
- `dashboard/app.js` — frontend logic (api, tables, jobs, settings, exports, SSE)
- `scripts/router.py` — Core routing: key rotation, circuit breakers, telemetry emission
- `scripts/router.py` — Core routing: key rotation, circuit breakers, telemetry emission
- `scripts/state.py` — Shared JSON state helpers
- `scripts/config.py` — Centralized `Config` dataclass
- `scripts/logging_setup.py` — Shared logging
- `scripts/key_pool.py`, `optimizer.py`, `orchestrator.py`, `quality_gate.py` — Supporting CLIs
- `config/*.txt` — Per-task ordered model lists
- `state/` — Runtime state (JSONL + JSON)
- `tests/` — unittest suite

## Common commands

```bash
python dashboard/server.py                    # start control panel
python -m unittest discover -s tests -v       # run tests
python -m py_compile scripts/*.py dashboard/server.py
shellcheck scripts/load-keys.sh
python scripts/router.py route --task code --prompt "..."
python scripts/router.py plan --task code
python scripts/router.py check
```

## Key design points

- Dashboard requires `X-Router-Token` on every `/api/*` call (constant-time compare)
- SSE `/api/stream` broadcasts state changes; polling fallback when tab hidden
- Audit log at `state/audit.jsonl`; `GET /api/audit` returns last 500 entries
- `POST /api/token/regen` rotates the per-process token
- Router classifier matches `500–504` only (not arbitrary `5xx`-looking numbers)
- Secrets never logged or printed; `last_line()` redacts secrets

## When making changes

1. Run `python -m py_compile scripts/*.py dashboard/server.py`
2. Run `python -m unittest discover -s tests -v`
3. Update `CHANGELOG.md` for user-visible changes
4. Keep dashboard modular: index.html (structure), style.css (styles), app.js (logic)
