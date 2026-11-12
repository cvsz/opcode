Here are all the refactor opportunities across the codebase, grouped by area:
1. Replace CLI subprocess with opencode v2 API (biggest win)
Current: dashboard/server.py and scripts/router.py shell out to opencode run via subprocess.Popen, then parse CLI text output heuristically in classify().
Refactor: Call opencode serve's /api/runs endpoint directly (POST for run, GET for status, DELETE for cancel), parse the NDJSON event stream instead of grepping text.
- Dashboard: start_job() worker calls /api/runs instead of subprocess.Popen([OPENCODE, "run", ...])
- Router: route() reads NDJSON stream from /api/runs, extracts final text part as answer, classifies from step_finish reason + error events
- Captures real tokens/cost from tokens/cost fields instead of estimating
- Eliminates last_line() secret-redaction heuristic — secrets never appear in structured events
2. Consolidate duplicated helpers
Current: dashboard/server.py and scripts/router.py each redefine load(), atomic_write(), JSON state read/write, summary()-like aggregation.
Refactor: Extract a shared module (e.g., scripts/state.py or lib/) with:
- load_json(path, default), save_json(path, data), atomic_write()
- State file paths (STATE/, CONFIG/) as constants
- Circuit/key-pool state structures
3. Add type hints throughout
Current: Minimal type annotations; functions use untyped params/returns.
Refactor: Add from __future__ import annotations (already in some files) plus explicit types for dicts, lists, Optional, Union — especially in classify(), key_candidates(), start_job(), summary().
4. Replace print() with the logging module
Current: print('[dashboard] ...'), print(f"[router] ...") scattered everywhere.
Refactor: Use logging.getLogger(__name__) with configurable level (LOG_LEVEL env var), structured formatting, file/stream handlers.
5. Frontend modularity
Current: Single dashboard/index.html with inline CSS + JS (~70 lines HTML, all logic inline).
Refactor: Split into:
- dashboard/index.html (structure only)
- dashboard/style.css (all styles)
- dashboard/app.js (all logic, split into modules: api, tables, jobs, settings, exports)
6. Strengthen authentication & session management
Current: Per-process random token, no rotation schedule, no session tracking, no rate limiting.
Refactor:
- Token rotation on schedule or after N requests
- Rate limiting per endpoint (token bucket)
- Optional --require-auth flag for stricter mode
- Audit log already exists; add IP/user-agent to entries
7. Replace JSONL with SQLite for state
Current: state/events.jsonl, state/audit.jsonl, state/circuits.json, state/key-pools.json — all flat files.
Refactor: SQLite database for events, audit log, circuits, key pools — enables queries (filter by model, time range, status) instead of reading entire files into memory.
8. Expand test coverage
Current: 67 tests pass, covering router classification, circuit breakers, key pools, dashboard auth, SSE, audit log, token regen, settings, and classifier regression.
Refactor: Add tests for:
- SSE /api/stream connection and broadcast
- POST /api/token/regen invalidating old token
- GET /api/audit entries after mutating calls
- Export endpoints (CSV/JSON content)
- Table sorting/filtering/pagination logic (JS unit tests)
- Classify with real NDJSON event structures
9. Async server instead of threading
Current: http.server.ThreadingHTTPServer with one thread per SSE connection + one thread per job.
Refactor: asyncio-based server (aiohttp or http.server with asyncio) — handles SSE + concurrent jobs more efficiently, simpler concurrency model than threads + locks.
10. Input validation with a schema library
Current: Manual validation (if not isinstance(model,str) or len(model)>200 ...).
Refactor: Use pydantic or marshmallow for request/response schemas — clearer errors, automatic documentation.
11. Configuration centralization
Current: Env vars scattered (CIRCUIT_FAILURE_THRESHOLD, OPENCODE_QUERY_TIMEOUT, ROUTER_AUTO, ADAPTIVE_ORDER, REQUIRE_ENV_KEY, etc.) with no single source of truth.
Refactor: Config dataclass loaded from env + config file, with defaults and validation at startup.
12. Dashboard → settings page persistence
Current: Theme/refresh stored in localStorage only; token regen reloads page.
Refactor: Persist settings to server-side state (per-token or per-client), so settings survive token regen without reload.
Recommended order:
1. Replace CLI subprocess with v2 API — eliminates the fragile text-parsing heuristic (the #1 reliability issue)
2. Consolidate duplicated helpers — reduces surface area for bugs
3. Add type hints + logging — maintainability
4. Frontend modularity — makes the UI maintainable as it grows
5. SQLite state — scalable querying
6. Expanded tests — confidence for the above changes
Want me to start on any of these? The v2 API migration (item 1) would have the biggest impact on reliability since it replaces the classify() text heuristic with structured events.