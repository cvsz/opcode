# GEMINI.md

# OpenCode Router — Gemini instructions

## What this project does

Multi-provider LLM router with:
- Provider-scoped multi-API-key rotation
- Circuit breakers (per model, per failure category)
- JSONL telemetry + audit log
- Hardened local control panel (token auth, CSP, SSE, job cancel, modular frontend: index.html + style.css + app.js)

## Entry points

- `python scripts/router.py route --task <task> --prompt <text>`
- `python scripts/router.py plan --task <task>`
- `python scripts/router.py check`
- `python dashboard/server.py` (control panel on http://127.0.0.1:8765)

## Testing

```bash
python -m unittest discover -s tests -v
```

## Key invariants

- Secrets are never printed or logged (`last_line()` redacts)
- Dashboard token compared with `hmac.compare_digest`
- Host/Origin guards on all dashboard requests
- Circuit opens after threshold failures; quota opens after 1
