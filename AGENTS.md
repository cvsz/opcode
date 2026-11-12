# AGENTS.md

# OpenCode Free-Model Router

Local multi-API-key router with circuit breakers, telemetry, and a hardened modular control-panel dashboard.

## Project structure

```
dashboard/         Control panel (modular frontend + Python HTTP server)
  index.html       HTML structure (references style.css + app.js)
  style.css        All styles (dark/light themes, responsive, ARIA)
  app.js           All frontend logic (api, tables, jobs, settings, exports, SSE, confirm dialog)
  server.py        HTTP API server: token auth, SSE, audit log, rate limiting, pydantic schemas
scripts/           Router, key pool, optimizer, orchestrator, quality gate, state, config, logging
  router.py        Multi-key failover routing, circuit breakers, classify, v2 API client (dual-path)
  state.py         Shared JSON state helpers
  config.py        Centralized Config dataclass
  logging_setup.py Shared logging
  db.py            SQLite state store (events, audit, circuits, key pools, settings)
  key_pool.py      Key pool management CLI
  optimizer.py     Telemetry-based model scoring (collect_stats / main)
  orchestrator.py  Multi-role orchestration (planner → coder → reviewer → tester → security_reviewer)
  quality_gate.py  Fail-closed quality gate checks
config/            Per-task ordered model lists (code, review, fast, vision, general)
state/             Runtime state (events.jsonl, circuits.json, key-pools.json, audit.jsonl,
                   dashboard-settings.json, router.db SQLite)
tests/             Python unittest suite (77 tests)
.github/           CI workflow, Dependabot, issue templates
Makefile           All-in-one control: start/stop/status/restart, test/lint/type-check/security-scan/ci-all
```

## How to run

```bash
# Start everything (dashboard + quality gate + tests) — one command
make all

# Start dashboard only (background service)
make start

# Stop all services
make stop

# Check service status
make status

# Restart all services
make restart

# Run dashboard control panel (http://127.0.0.1:8765)
python dashboard/server.py --host 127.0.0.1 --port 8765

# Run the test suite (77 tests)
python -m unittest discover -s tests -v

# Compile-check all Python
python -m py_compile scripts/*.py dashboard/server.py

# Router CLI
python scripts/router.py route --task code --prompt "hello"
python scripts/router.py plan --task code
python scripts/router.py check

# Quality gate (fail-closed)
python scripts/quality_gate.py

# Full CI: lint + type-check + security + tests
make ci-all

# Security scan only
make security-scan   # bandit + pip-audit

# Frontend check
make frontend-check

# Backend checks
make backend-lint backend-type-check backend-security
```

## Key environment variables

| Variable | Default | Purpose |
|---|---|---|
| `CIRCUIT_FAILURE_THRESHOLD` | `2` | Failures before circuit opens |
| `CIRCUIT_COOLDOWN_SECONDS` | `300` | Circuit cooldown (non-quota) |
| `CIRCUIT_QUOTA_COOLDOWN_SECONDS` | `3600` | Circuit cooldown (quota exhausted) |
| `KEY_COOLDOWN_SECONDS` | `300` | Key cooldown after auth failure |
| `OPENCODE_QUERY_TIMEOUT` | `30` | Catalogue query timeout |
| `TIMEOUT_SECONDS` | `180` | Per-run timeout |
| `ADAPTIVE_ORDER` | `0` | Reorder by smoothed success rate |
| `REQUIRE_ENV_KEY` | `0` | Skip models without env keys |
| `ROUTER_AUTO` | `0` | Pass `--auto` to `opencode run` |
| `ROUTER_ARCHIVED` | `1` | Pass `--archived` when supported |
| `DASHBOARD_HOST` | `127.0.0.1` | Dashboard bind host |
| `DASHBOARD_PORT` | `8765` | Dashboard bind port |
| `DASHBOARD_TOKEN_BYTES` | `24` | API token length |
| `LOG_LEVEL` | `INFO` | Logging level |
| `OPENCODE_SERVER` | unset | if set, router calls `opencode serve` v2 API instead of CLI subprocess |

## Testing

```bash
python -m unittest discover -s tests -v
```

77 tests cover router classification, circuit breakers, key pools, dashboard auth, SSE, audit log, settings, export, sorting/filtering/pagination, classifier regression, and security regression (command injection neutralization, SSRF URL scheme validation).

## Linting

```bash
shellcheck scripts/load-keys.sh
make lint          # ruff check
make type-check    # mypy
make security-scan # bandit + pip-audit
make ci-all        # lint + type-check + security + tests
```

## Key features summary

### Backend (`scripts/router.py`, `dashboard/server.py`)
- Provider-scoped multi-API-key failover with cooldowns
- Per-model circuit breakers (500–504 outage classification only)
- Provider error classification (quota, rate limit, auth, outage, model unavailable, execution error)
- Dual execution: legacy `opencode run` CLI + `opencode serve` v2 API NDJSON
- SQLite state (dual-write with JSONL for backward compat)
- Pydantic request/response schemas with manual fallback
- Logging module throughout (replaces `print()`)
- Type hints throughout
- Security: SSRF protection via URL scheme validation (http/https only)
- Security: Command injection prevention — `shell=False` for `TEST_COMMAND`, uses `shlex.split()`

### Frontend (`dashboard/app.js`, `dashboard/style.css`, `dashboard/index.html`)
- Modular: HTML structure / CSS styles / JS logic
- SSE real-time updates with polling fallback
- Token auth, rate limiting, audit log, settings persistence
- Table sorting/filtering/pagination, job detail view, CSV/JSON export
- Dark/light theme, ARIA live regions, focus-visible, keyboard-navigable
- Custom confirm dialogs, loading states, toasts
