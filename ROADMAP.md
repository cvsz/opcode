# ROADMAP.md

# OpenCode Router Roadmap

## Completed (v5 + hardening)

- Python router with multi-key failover and circuit breakers
- JSONL telemetry + audit log
- Hardened control panel (token auth, CSP, SSE, job cancel, export)
- CI matrix (Python 3.10–3.13), Dependabot
- Classifier fix: `500–504` only for outages

## In progress (refactor per MASTER.md)

1. Replace CLI subprocess with `opencode serve` v2 API (`/api/runs` NDJSON)
2. Consolidate duplicated helpers into `scripts/state.py` / `config.py`
3. Type hints + logging throughout
4. Frontend modularity (CSS + JS split)
5. SQLite state instead of JSONL
6. Expanded test coverage

## Planned

- Real-time SSE dashboard (already basic; enhance with job streams)
- Table sorting/filtering/pagination
- Job detail view with expandable output
- Settings persistence server-side
- Token rotation schedule + rate limiting
- pydantic input validation
- Async server (aiohttp/asyncio)

## Not planned

- User login / multi-tenancy (single-user local control panel by design)
- `--auto` permission bypass by default
- Provider-native structured error codes (depends on OpenCode CLI)
