# ROADMAP.md

# OpenCode Router Roadmap

## Completed (v5 + hardening)

- Python router with multi-key failover and circuit breakers
- JSONL telemetry + audit log
- Hardened control panel (token auth, CSP, SSE, job cancel, export)
- CI matrix (Python 3.10–3.13), Dependabot
- Classifier fix: `500–504` only for outages
- Command injection fix: `quality_gate.py` no longer uses `shell=True`
- SSRF fix: `urlopen` restricted to `http://` / `https://` schemes

## Completed (full refactor per MASTER.md)

1. Replace CLI subprocess with `opencode serve` v2 API (`/api/runs` NDJSON) with legacy CLI fallback
2. Consolidate duplicated helpers into `scripts/state.py` / `config.py`
3. Type hints + logging throughout
4. Frontend modularity (CSS + JS split)
5. SQLite state instead of JSONL (dual-write for backward compat)
6. Expanded test coverage (77 tests)
7. Dashboard hardening: rate limiting, IP/UA audit log, token rotation, settings persistence
8. Pydantic validation with CI-safe manual fallback

## Completed (enterprise hardening, 2026-11-12)

- Structured JSON logging (`LOG_FORMAT=json`) for router + dashboard
- SQLite WAL mode with per-statement connections; concurrent-write test
- Request schema validation (bounded prompt/task/model/limit values)
- `/api/health` deep-check endpoint for external monitors
- Performance caches: model catalogue, circuit/key-pool state, task routes, dashboard summary
- Frontend chat interface on the free-model `general` route
- Security fuzzer (`scripts/security_fuzzer.py`) — server answers 4xx, never crashes
- All-in-one Makefile control center (`make all`, `ci-all`, service lifecycle)
- 79 tests green; ruff, mypy, bandit (HIGH=0) clean

## Completed (enterprise final pass, 2026-11-12)

- **Log rotation**: `RotatingFileHandler` in `logging_setup.py`; dashboard CLI args for log file config
- **Structured NDJSON parsing**: `classify_from_ndjson()` for `opencode run --format json`; graceful malformed line handling
- **Enhanced `/api/health`**: deep checks for `opencode_serve_reachable`, `circuits_healthy`, `keys_healthy`, `open_circuits`, `total_key_slots`
- **Request correlation IDs**: `X-Request-ID` header propagated through audit log, SQLite events, router subprocess
- **Integration test suite**: `tests/test_integration.py` (full pipeline, circuit breaker, key pool, audit log)
- **Test suite expanded**: 79 → 96 tests (NDJSON classify, correlation IDs, integration)
- **Documentation sync**: README, SECURITY, AGENTS, ROADMAP, IMPLEMENTATION-CHECKLIST, MASTER, CHANGELOG updated

## Planned (optional)

- Real-time SSE dashboard enhancement with job streams
- Async server (aiohttp/asyncio)
- JS unit tests for frontend tables/exports

## Not planned

- User login / multi-tenancy (single-user local control panel by design)
- `--auto` permission bypass by default
- Provider-native structured error codes (depends on OpenCode CLI)
