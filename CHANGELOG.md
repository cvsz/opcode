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
- Expanded tests: 16 -> 77 (SSE, token regen, audit log, settings, export, sorting/filtering/pagination, security regression)

## Enterprise final pass (2026-11-12)
- **Log rotation**: `scripts/logging_setup.py` adds `RotatingFileHandler` support; dashboard configurable via `--log-file`, `--log-max-bytes`, `--log-backup-count`; correlation IDs (`request_id`) propagate through router → dashboard → audit log → SQLite.
- **Structured NDJSON parsing**: `classify_from_ndjson()` handles `opencode run --format json` output; graceful handling of malformed/incomplete lines; enables `--format json` path via `OPENCODE_FORMAT_JSON=1`.
- **Enhanced `/api/health`**: deep checks for `opencode_serve_reachable`, `circuits_healthy`, `keys_healthy`, `open_circuits`, `total_key_slots`; status `ok`/`degraded`.
- **Request correlation IDs**: `X-Request-ID` header (auto-generated if missing) included in audit log, SQLite events, and router subprocess via `ROUTER_REQUEST_ID` env var.
- **Integration test suite**: `tests/test_integration.py` covers full pipeline (dashboard → router → state), circuit breaker, key pool, audit log.
- **Test suite expanded**: 79 → 96 tests (NDJSON classify, correlation IDs, integration).
- **Documentation sync**: README, SECURITY, AGENTS, ROADMAP, IMPLEMENTATION-CHECKLIST, MASTER updated.

## Enterprise hardening (2026-11-12)
- **Structured logging**: `LOG_FORMAT=json` emits one JSON object per line from router and dashboard (`scripts/logging_setup.py` `JsonFormatter`); extra fields preserved.
- **SQLite WAL + per-statement connections**: `scripts/db.py` refactored to connection-per-operation with `journal_mode=WAL`, `synchronous=NORMAL`, 30 s busy timeout; verified lossless under 10-thread concurrent writes (`tests/test_sqlite_concurrency.py`).
- **Request schema hardening**: `RunRequest` bounds prompt (≤1,000,000 chars), task (≤50), model (≤200), `max_models` (0–100), `timeout` (0–3600).
- **`/api/health` deep-check endpoint**: returns status, version, key-pool slot health, and circuit state for external monitors.
- **Performance caches**: model catalogue (30 s TTL), circuit/key-pool state (5 s TTL), task route lists, dashboard summary (2 s) and key status (5 s) caches.
- **Frontend chat interface**: multi-turn chat panel routed through the `general` free-model task route with in-memory history and automatic job polling.
- **Security fuzzer**: `scripts/security_fuzzer.py` sends oversized / out-of-range / invalid payloads at `/api/run`; server answers 4xx and never crashes.
- **Makefile all-in-one control center**: `make all/start/stop/status/restart/ci-all/dev` plus feature-group targets.
- Tests: 77 -> 79 (JSON logging, SQLite concurrency).

## Security fixes (hardening)
- Command injection neutralized: `quality_gate.py` no longer uses `shell=True`; `TEST_COMMAND` parsed with `shlex.split()` and executed via `subprocess.run(cmd, shell=False)`
- SSRF neutralized: all `urllib.request.urlopen` calls in `router.py` validate URL scheme (`http://`/`https://` only); `file://`, `ftp://`, `gopher://` rejected via `_validate_url()`
- Bandit: HIGH=0 (was 1), MEDIUM=3 (mitigated), LOW=14 (accepted/false positive)

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


## OPCODE 5.1.0 — NVIDIA Direct Provider (2026-11-14)

- Added direct NVIDIA NIM `/v1/chat/completions` execution to the canonical Bash CLI; NVIDIA models no longer pass through `opencode run`.
- Kept identifier boundaries explicit: catalog `nvidia/<publisher>/<model>:free`, OPCODE runtime `nvidia/<publisher>/<model>`, NVIDIA API payload `<publisher>/<model>`.
- Added provider-level RPM limiting, bounded request timeouts, per-model cooldowns for rate-limit/model rejection, key rotation on authentication failures, and redirect refusal for bearer-authenticated requests.
- Added NVIDIA numbered key slots (`NVIDIA_API_KEY_1..NVIDIA_API_KEY_8`) and single/pool key support.
- Added task-specific ordered route allowlists for code, review, fast, general, vision, and agent execution; NVIDIA direct models are excluded from `agent_execution`.
- Integrated the same direct NVIDIA discovery and inference path into `scripts/router.py` for `make route`.
- Added local HTTP integration tests covering catalog discovery, canonical/native ID conversion, direct inference, key rotation, provider filtering, and redirect rejection.
- Safety boundary: `:free` is a catalog suffix and allowlist convention, not evidence of current pricing, quota, or entitlement. `OPCODE_FREE_ONLY=true` still requires `verified_free`.
- Direct NVIDIA adapter is text inference only; it does not provide OpenCode tools or filesystem execution.

## OPCODE 5.1.1 — NVIDIA Build Free Endpoint Catalog (2026-11-14)

- Added `scripts/nvidia_build_catalog.py`: discovers models from `build.nvidia.com` via RSC flight-data parsing with pagination, retries, backoff, rate limiting, and SSRF-safe URL validation.
- Classifies endpoint types (`Free Endpoint` / `Partner Endpoint` / `Download Available`) as distinct; only `Free Endpoint` is eligible for free inference routing.
- Free eligibility never inferred from `/v1/models`, model names, `:free` suffixes, or preview status.
- Resolves NIM API model IDs from official `.md` detail-page prototype snippets; falls back to native `<publisher>/<name>`.
- Snapshots preserve SHA-256 provenance, verification timestamps, `dateCreated`/`dateModified`, `api_model_id`, canonical URL, and source URL.
- Change detection flags deprecated / removed / unavailable / changed models; flagged models are never silently promoted.
- Added `opcode catalog sync-nvidia-build` (discovery only, `eligibility: unknown`) and `opcode catalog verify-nvidia` (explicit verification → `verified_free`). Sync never silently upgrades unknown → verified_free.
- Added Makefile targets `nvidia-build-sync` and `nvidia-verify`.
- Added `tests/test_nvidia_build_catalog.py` (33 tests): realistic catalog fixtures, malformed responses, change detection, snapshot round-trip, idempotency, and the never-upgrade-unknown safety invariant.
- Updated `config/nvidia-free-models.txt` with Build-discovered free-endpoint models.
- Capability-aware routing inferred from catalog `general` labels (vision, reasoning, coding, tool_calling, embeddings, architecture, agent_execution, general); incompatible capabilities rejected for specialized tasks.
- 143/143 tests pass (110 existing + 33 new); lint/type/security clean for new files.

## OPCODE 5.1.2 — OpenRouter Free Models (2026-11-14)

- Added `config/openrouter-free-models.txt`: explicit OpenRouter free endpoint allowlist (17 models), mirroring the NVIDIA Build `nvidia-free-models.txt` pattern. Kept distinct from `Downloadable` / `Partner Endpoint` classifications.
- OpenRouter free eligibility established via `opcode catalog sync-openrouter` (zero-priced entries from `/v1/models` marked `verified_free`) — never inferred from model names or `:free` suffixes alone.
- Added missing OpenRouter free models to `config/free-models.txt` (baidu/ernie-5.5-flash-preview, dots-studio/dots-3-note-preview, google/lyria-3-clip-preview, google/lyria-3-pro-preview, inclusionai/ling-3.2-flash, particle-ai/particle-1).
- 143/143 tests pass; compile clean.

## OPCODE 5.1.3 — OpenCode + Kilo Free Models (2026-11-14)

- Added `config/opencode-free-models.txt`: explicit OpenCode free endpoint allowlist (10 models), mirroring the NVIDIA Build `nvidia-free-models.txt` pattern.
- Added `config/kilo-free-models.txt`: explicit Kilo free endpoint allowlist (10 models), mirroring the NVIDIA Build `nvidia-free-models.txt` pattern.
- Reorganized `config/free-models.txt` by provider (opencode, kilo, openrouter, other) and removed duplicate entries.
- OpenCode / Kilo free eligibility established via `catalog import` from free-labelled catalogue entries — never inferred from model names or `:free` suffixes alone. Imported models remain `unknown` until explicitly verified.
- 143/143 tests pass; compile clean.
