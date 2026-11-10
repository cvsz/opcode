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

## Known gaps (unchanged from source)
- `router.py` events no longer carry `event_id` (dropped in v4); nothing consumes it.
- v2 vision/general candidate lists contained two extra `gemma-4-*` entries removed in v3; left removed.
