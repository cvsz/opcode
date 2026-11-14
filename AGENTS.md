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
  logging_setup.py Shared logging (text + JSON structured output via LOG_FORMAT=json)
  db.py            SQLite state store (events, audit, circuits, key pools, settings; WAL mode)
  key_pool.py      Key pool management CLI
  optimizer.py     Telemetry-based model scoring (collect_stats / main)
  orchestrator.py  Multi-role orchestration (planner → coder → reviewer → tester → security_reviewer)
  quality_gate.py  Fail-closed quality gate checks
  security_fuzzer.py  Adversarial payload fuzzer for the dashboard API
config/            Per-task ordered model lists (code, review, fast, vision, general)
state/             Runtime state (events.jsonl, circuits.json, key-pools.json, audit.jsonl,
                   dashboard-settings.json, router.db SQLite WAL)
tests/             Python unittest suite (96 tests incl. JSON logging + SQLite concurrency + NDJSON classify + correlation IDs + integration)
.github/           CI workflow, Dependabot, issue templates
docs/              Plans, specs, and Final-Report.md
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

# Run the test suite (79 tests)
python -m unittest discover -s tests -v

# Compile-check all Python
python -m py_compile scripts/*.py dashboard/server.py

# Router CLI
python scripts/router.py route --task code --prompt "hello"
python scripts/router.py plan --task code
python scripts/router.py check

# NVIDIA Build catalog
./opcode catalog sync-nvidia-build    # Discover + classify Build Free Endpoint models
./opcode catalog verify-nvidia        # Verify Free Endpoint eligibility -> verified_free
./opcode models list --provider nvidia --free-only
./opcode route explain --task coding  # NVIDIA models rank first when eligible

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
|----------|---------|---------|
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
| `LOG_FORMAT` | `text` | `json` for structured JSON logging (router + dashboard) |
| `OPENCODE_MODELS_CACHE_TTL` | `30` | seconds to cache `opencode models` output |
| `CIRCUIT_CACHE_TTL` / `KEY_POOL_CACHE_TTL` | `5` | seconds to cache circuit / key-pool state reads |
| `OPENCODE_SERVER` | unset | if set, router calls `opencode serve` v2 API (`/api/runs`) instead of CLI subprocess |
| `OPENCODE_FORMAT_JSON` | `0` | `1` enables `opencode run --format json` for structured NDJSON output |
| `DASHBOARD_LOG_FILE` | `state/dashboard.log` | Dashboard log file path |
| `DASHBOARD_LOG_MAX_BYTES` | `5000000` | Max bytes per log file before rotation |
| `DASHBOARD_LOG_BACKUP_COUNT` | `5` | Number of backup log files to keep |

## Testing

```bash
python -m unittest discover -s tests -v
```

96 tests cover router classification, circuit breakers, key pools, dashboard auth, SSE, audit log, settings, export, sorting/filtering/pagination, classifier regression, security regression (command injection neutralization, SSRF URL scheme validation), NDJSON classify, correlation IDs, and integration.

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
- Provider error classification (quota_exhausted, rate_limited, authentication, provider_outage, model_unavailable, execution_error)
- Dual execution: legacy `opencode run` CLI + `opencode serve` v2 API NDJSON
- SQLite state (dual-write with JSONL for backward compatibility; WAL journal mode, 30 s busy timeout)
- Pydantic request/response schemas with manual fallback and bounded field values
- Logging module throughout (replaces `print()`); `LOG_FORMAT=json` for structured logs
- Performance caches: model catalogue, circuit/key-pool state, task routes, dashboard summary
- Type hints throughout
- Security: SSRF protection via URL scheme validation (http/https only)
- Security: Command injection prevention — `shell=False` for `TEST_COMMAND`, uses `shlex.split()`

### Frontend (`dashboard/app.js`, `dashboard/style.css`, `dashboard/index.html`)
- Modular: HTML structure / CSS styles / JS logic
- Chat interface routed through the free-model `general` task
- SSE real-time updates with polling fallback
- Token auth, rate limiting, audit log, settings persistence
- Table sorting/filtering/pagination, job detail view, CSV/JSON export
- Dark/light theme, ARIA live regions, focus-visible, keyboard-navigable
- Custom confirm dialogs, loading states, toasts

### NVIDIA Build Free Endpoint catalog (`scripts/nvidia_build_catalog.py`)
- Discovers models from `build.nvidia.com` via RSC flight-data parsing with pagination
- Classifies endpoint types: `Free Endpoint` / `Partner Endpoint` / `Download Available`
  (kept distinct; only `Free Endpoint` grants free inference eligibility)
- Free eligibility never inferred from `/v1/models`, model names, `:free` suffixes, or preview status
- Resolves NIM API model IDs from `.md` detail-page prototype snippets
- Snapshots preserve provenance, SHA-256, verification timestamps, and change detection
  (deprecated / removed / unavailable / changed models flagged, never silently promoted)
- Catalog sync records discovery with `eligibility: unknown` — never silently upgrades
  to `verified_free`. `opcode catalog verify-nvidia` is the explicit verification step.
- Capabilities inferred from catalog `general` labels (vision, reasoning, coding,
  tool_calling, embeddings, architecture, agent_execution, general)
- Retries, backoff, rate limiting, SSRF URL-scheme validation; API keys never logged