# IMPLEMENTATION-CHECKLIST.md

# Implementation Checklist

## Refactor — all 12 items (MASTER.md)

### Phase 1: Foundation ✅
- [x] scripts/state.py — shared JSON state helpers
- [x] scripts/config.py — centralized Config dataclass
- [x] scripts/logging_setup.py — shared logging (with RotatingFileHandler support)
- [x] scripts/router.py — type hints, logging, module-level patchable state
- [x] tests pass (96 total)

### Phase 2: CLI subprocess → opencode v2 API ✅
- [x] scripts/router.py: route() calls POST /api/runs (v2 API), parses NDJSON when OPENCODE_SERVER set
- [x] classify_from_events(): parses step_finish reason + error events from NDJSON
- [x] classify_from_ndjson(): parses `opencode run --format json` output
- [x] v2_runs_create/get/cancel() helpers with Bearer auth
- [x] Legacy CLI subprocess path preserved (used by existing tests)
- [x] Dual-path: v2 API when OPENCODE_SERVER env set, CLI otherwise
- [x] OPenCODE_FORMAT_JSON=1 enables structured CLI output path

### Phase 3: Dashboard hardening ✅
- [x] Rate limiting per endpoint (token bucket, 30 req/60s)
- [x] Audit log: IP + user-agent in entries
- [x] Token rotation after 1000 requests + /api/token/regen
- [x] Settings persistence server-side (/api/settings)
- [x] Log rotation via RotatingFileHandler (configurable via CLI)
- [x] Request correlation IDs (X-Request-ID header propagated throughout)

### Phase 4: State storage ✅
- [x] scripts/db.py — SQLite for events, audit, circuits, key pools, settings
- [x] Dual-write: JSONL + SQLite for backward compat
- [x] Queries: filter by model, status, time range
- [x] request_id column in events table for correlation

### Phase 5: Frontend modularity ✅
- [x] dashboard/index.html — structure only (references style.css + app.js)
- [x] dashboard/style.css — all styles
- [x] dashboard/app.js — modules: api, tables, jobs, settings, exports, SSE, confirm dialog

### Phase 6: Validation ✅
- [x] pydantic schemas with manual fallback (CI-safe: no pydantic install required)
- [x] Fallback BaseModel accepts **kwargs when pydantic unavailable

### Phase 7: Async server (optional)
- [ ] aiohttp or asyncio http.server (ThreadingHTTPServer works fine; optional)

### Phase 8: Expanded tests ✅
- [x] 96 tests total (up from 77)
- [x] SSE /api/stream connection + broadcast (tested via server)
- [x] POST /api/token/regen invalidating old token
- [x] GET /api/audit entries after mutating calls
- [x] GET/POST /api/settings
- [x] Dashboard auth, headers, Host guard, cancel
- [x] Security regression: command injection neutralization
- [x] Security regression: SSRF URL scheme validation
- [x] classify_from_ndjson() tests (success, error, malformed, tool-calls)
- [x] Correlation ID propagation tests
- [x] Integration test suite (full pipeline, circuit breaker, key pool, audit)

## Docs ✅
- [x] AGENTS.md, CLAUDE.md, CODEX.md, GEMINI.md
- [x] CODE_OF_CONDUCT.md, CONTRIBUTING.md, GOVERNANCE.md, ROADMAP.md, SECURITY.md
- [x] .github/ISSUE_TEMPLATE/ (bug_report.md, feature_request.md, security.yml)
- [x] .github/dependabot.yml
- [x] CHANGELOG.md (updated with enterprise final pass)
- [x] IMPLEMENTATION-CHECKLIST.md, MASTER.md, OPENCODE.md

## Verification ✅
- [x] python -m py_compile scripts/*.py dashboard/server.py — clean
- [x] python -m unittest discover -s tests -v — 96 tests, OK
- [x] No committed __pycache__ or .pyc files
- [x] Rate limiting, token rotation, audit log all functional
- [x] CI passes on Python 3.10–3.13
- [x] Security: command injection neutralized (shell=False)
- [x] Security: SSRF neutralized (url scheme validation http/https only)
- [x] Log rotation functional (RotatingFileHandler)
- [x] Correlation IDs propagate end-to-end
