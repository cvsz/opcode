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
- CI workflow (`.github/workflows/ci.yml`); 16 tests including an end-to-end route test against a fake `opencode`.

## Not done / needs real data
- `opencode-cli-errors.txt` is empty, so error patterns in `classify()` are still unverified against real CLI output. Capture a few real failures (bad key, rate limit, unknown model) and add them as test fixtures.
- `opencode run --format json` could give structured results but its schema isn't in the export.
- `auth export` prints secrets; the router deliberately never calls it.

## Known gaps (unchanged from source)
- `router.py` events no longer carry `event_id` (dropped in v4); nothing consumes it.
- v2 vision/general candidate lists contained two extra `gemma-4-*` entries removed in v3; left removed.
