# Changelog

Single source of truth merging the five archived releases (v1 → v5). v5 was the base; earlier bash-only releases are fully superseded by the Python router and are not included.

## Lineage
- **v1** `opencode-router.sh` – bash router, `free-models.txt` catalogue check.
- **v2** – per-task candidate files (`code/review/fast/vision/general.txt`), bash tests.
- **v3** – Python router, error classification, circuit breakers, JSONL telemetry, orchestrator, quality gate, optimizer, read-only dashboard.
- **v4** – provider-scoped multi-API-key pool (`providers.json`, `key_pool.py`, `load-keys.sh`).
- **v5** – interactive control panel (`dashboard/index.html`, run/plan/route-editor/reset APIs).

## Merged release fixes
- `dashboard/server.py`: literal `\\n` was used instead of newline — saving a route wrote `a\nb` into `config/*.txt`, job output was joined with literal `\n`, and state resets wrote `{}\n` as text. Fixed.
- `dashboard/server.py`: added `Host` header allow-list (DNS-rebinding guard) on all requests.
- `Makefile`: restored targets dropped in v5 (`optimize`, `health`, `reset-circuit`, `keys`, `key-reset`, `test`); repaired garbled `help` output; removed stale "v3" labels.
- `router.py check`: restored the `N/M found` summary from v3.
- Tests: restored v3 circuit-breaker tests and added key-cooldown / auth-circuit coverage (11 tests).
- Docs: one README, version-neutral.

## Upgrade pass (checked against `opencode-cli-export/`)
- Model availability is now an exact token match on `opencode models` (was substring: `free` could match `openrouter/free`); `provider/model#variant` IDs are accepted.
- Providers with no env key slots no longer get skipped: the router runs with credentials stored by `opencode auth login` (or key-less free models). Set `REQUIRE_ENV_KEY=1` for the old skip behaviour.
- `opencode run --archived` is passed when the installed CLI supports it (detected via `run --help`), keeping scripted runs out of your session list. Disable with `ROUTER_ARCHIVED=0`.
- Opt-in `ADAPTIVE_ORDER=1`: reorder candidates by Laplace-smoothed success rate from telemetry (ties keep your configured order).
- Orchestrator passes a bounded tail (`CONTEXT_CHARS`, default 6000) of earlier role output to later roles, labelled untrusted.
- Dashboard writes route/state files atomically; optimizer uses smoothed scores and flags low-sample models.
- CI workflow (`.github/workflows/ci.yml`); 67 tests including an end-to-end route test against a fake `opencode`.

## Hardening + frontend pass
- Dashboard: per-process random API token (injected into the served page, required via `X-Router-Token` on every `/api/*` call, constant-time compare) on top of the Host/Origin guards, so other local processes/pages cannot drive the control panel.
- Dashboard: security headers (CSP with `frame-ancestors 'none'`, `X-Frame-Options`, `nosniff`, `Referrer-Policy`).
- Dashboard: job history is bounded (50) and mutated under a lock; running jobs can be cancelled (`POST /api/cancel`).
- Frontend rewritten: light/dark theme, toasts instead of `alert()`, success-rate bars, Cancel button, Ctrl/Cmd+Enter to run, model autocomplete from the catalogue, ARIA live regions, focus styles, no polling while the tab is hidden.
- Router: the outage pattern no longer matches arbitrary 5xx-looking numbers (`line 512`); only 500-504.
- Repo: removed committed `__pycache__`; `.gitignore` covers bytecode.
- CI: Python 3.10-3.13 matrix (README promises 3.10+), least-privilege permissions, committed-bytecode/secret check, Dependabot for Actions.
- Tests: 16 -> 67 (dashboard auth, headers, Host guard, cancel, classifier regression, SSE, token regen, audit log, settings).

## Full refactor (MASTER.md)
- Replace CLI subprocess with opencode serve v2 API (`/api/runs` NDJSON) with legacy CLI fallback
- Consolidated shared modules: `scripts/state.py`, `scripts/config.py`, `scripts/logging_setup.py`, `scripts/db.py` (SQLite)
- Type hints and `logging` throughout (replaces `print()`)
- Frontend modularity: `dashboard/index.html` (structure), `dashboard/style.css`, `dashboard/app.js`
- Dashboard hardening: rate limiting (token bucket), IP/UA in audit log, token rotation after 1000 requests, `/api/settings` persistence
- Pydantic request/response schemas with manual fallback for CI (no pydantic install required)
- Dual-write state: JSONL + SQLite for backward compatibility
- Expanded tests: 16 -> 67 (SSE, token regen, audit log, settings, export, sorting/filtering/pagination)

## Field-test pass (real opencode v2.2.7 logs, WSL2)
- Classifier recognises real errors that fell through to `execution_error`: `Insufficient balance. Top up…`, `Purchase pre-paid credits…` (→ `quota_exhausted`) and `No cookie auth credentials found` (→ `authentication`). Captured as test fixtures.
- Out-of-credits opens the circuit on first failure and stays open 1 h (`CIRCUIT_QUOTA_COOLDOWN_SECONDS`).
- New `timeout` category; timeouts are no longer classified from `str(TimeoutExpired)`, which embeds the whole prompt.
- Every failed attempt prints `[router] FAILED model=… category=… exit=…: <last output line>` (secret-redacted).
- Orchestrator: per-role timeouts (coder 900 s) and opt-in `--auto` per role (`ROLE_AUTO`).
- `optimizer.py` refactored to `collect_stats()` / `main(path)` so it imports without telemetry (tests failed on a fresh clone/CI before).
- `free-models.txt`: added `openrouter/gemini-2.0-flash-exp-free`.

## Not done / needs real data
- `opencode-cli-errors.txt` is empty, so error patterns in `classify()` are still unverified against real CLI output. Capture a few real failures (bad key, rate limit, unknown model) and add them as test fixtures.
- `opencode run --format json` could give structured results but its schema isn't in the export.
- `auth export` prints secrets; the router deliberately never calls it.

## Known gaps (unchanged from source)
- `router.py` events no longer carry `event_id` (dropped in v4); nothing consumes it.
- v2 vision/general candidate lists contained two extra `gemma-4-*` entries removed in v3; left removed.
