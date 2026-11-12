# AGENTS.md

# OpenCode Router

Local multi-API-key router with circuit breakers, telemetry, and a hardened control-panel dashboard.

## Project structure

```
dashboard/         Control panel (HTML + Python HTTP server)
  index.html       Frontend (single file)
  server.py        HTTP API + SSE stream + audit log
scripts/           Router, key pool, optimizer, orchestrator, quality gate
  router.py        Multi-key failover routing with circuit breakers
  state.py         Shared JSON state helpers
  config.py        Centralized configuration (Config dataclass)
  logging_setup.py Shared logging
  key_pool.py      Key pool management CLI
  optimizer.py     Telemetry-based model scoring
  orchestrator.py  Multi-role orchestration
  quality_gate.py  Quality gate checks
config/            Per-task ordered model lists
state/             Runtime state (events, circuits, keys, audit)
tests/             Python unittest suite
.github/           CI + Dependabot
```

## How to run

```bash
# Start the dashboard control panel (http://127.0.0.1:8765)
python dashboard/server.py

# Run the test suite
python -m unittest discover -s tests -v

# Compile-check all Python
python -m py_compile scripts/*.py dashboard/server.py

# Router CLI
python scripts/router.py route --task code --prompt "hello"
python scripts/router.py plan --task code
python scripts/router.py check
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

## Testing

```bash
python -m unittest discover -s tests -v
```

67 tests cover router classification, circuit breakers, key pools, dashboard auth, SSE, audit log, and classifier regression.

## Linting

```bash
shellcheck scripts/load-keys.sh
make -n help test keys optimize
```
