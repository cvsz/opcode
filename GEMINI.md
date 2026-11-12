# GEMINI.md

# OpenCode Router — Gemini instructions

## What this project does

Multi-provider LLM router with:
- Provider-scoped multi-API-key rotation with cooldowns
- Circuit breakers (per model, per failure category; 500–504 outage classification only)
- JSONL telemetry + SQLite audit log (dual-write)
- Hardened local control panel (token auth, CSP, SSE, job cancel, modular frontend)
- Role-based orchestration (planner → coder → reviewer → tester → security_reviewer)
- Fail-closed quality gate
- Telemetry-based model ranking (Laplace-smoothed)
- Dual execution: legacy `opencode run` CLI + `opencode serve` v2 API NDJSON

## Entry points

- `python scripts/router.py route --task <task> --prompt <text>`
- `python scripts/router.py plan --task <task>`
- `python scripts/router.py check`
- `python dashboard/server.py` (control panel on http://127.0.0.1:8765)
- `make all` — start dashboard + run quality gate + run tests (one command)

## Testing

```bash
python -m unittest discover -s tests -v
```

77 tests covering router classification, circuit breakers, key pools, dashboard auth, SSE, audit log, settings, export, sorting/filtering/pagination, classifier regression, and security regression (command injection neutralization, SSRF URL scheme validation).

## Key invariants

- Secrets are never printed or logged (`last_line()` redacts)
- Dashboard token compared with `hmac.compare_digest`; rotates after 1000 requests
- Host/Origin guards on all dashboard requests; non-loopback only
- Circuit opens after threshold failures; quota opens after 1
- Pydantic validation with manual fallback; logging module throughout (no `print()`)
- Type hints throughout; modular frontend (HTML/CSS/JS split)
- Security: `TEST_COMMAND` executed without `shell=True`
- Security: `urlopen` restricted to `http://` / `https://` schemes
