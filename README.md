# OpenCode Free-Model Router

Free-model router for the OpenCode CLI: ordered per-task fallbacks, provider error classification, per-model circuit breakers, provider-scoped multi-API-key failover, role-based orchestration, a fail-closed quality gate, telemetry-based ranking, and a modular local control panel. See `CHANGELOG.md` for lineage.

## GitHub repository features

| Feature | File | Purpose |
|---|---|---|
| CI workflow | `.github/workflows/ci.yml` | Tests on Python 3.10–3.13 matrix (fail-fast off), least-privilege `contents: read` permissions, `py_compile`, `unittest`, `shellcheck`, Make-target parse check, committed-bytecode/secret scan |
| Dependabot | `.github/dependabot.yml` | Weekly automatic updates for GitHub Actions |
| Bug report | `.github/ISSUE_TEMPLATE/bug_report.md` | Structured bug reports with reproduction steps and environment |
| Feature request | `.github/ISSUE_TEMPLATE/feature_request.md` | Structured feature/enhancement proposals |
| Security advisory | `.github/ISSUE_TEMPLATE/security.yml` | Private security vulnerability reporting form |

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution workflow and [GOVERNANCE.md](GOVERNANCE.md) for decision-making.

## Project features

### Router (`scripts/router.py`)
- **Multi-API-key automatic switching**: ordered key slots per provider, secrets read from env vars only, injected into the OpenCode child process under the provider's auth env var; keys never printed or persisted
- **Per-model circuit breakers**: opens after configurable failure threshold (default 2); authentication/model-not-found opens after one failure; quota opens on first failure with a 1-hour cooldown
- **Provider error classification**: `quota_exhausted`, `rate_limited`, `authentication`, `provider_outage` (500–504 only), `model_unavailable`, `execution_error` — heuristic based on OpenCode CLI output
- **Dual execution paths**: legacy `opencode run` CLI subprocess, plus `opencode serve` v2 API (`POST /api/runs` NDJSON stream) when `OPENCODE_SERVER` is set — captures real token/cost fields instead of estimating
- **Adaptive model ordering**: Laplace-smoothed success rate reordering when `ADAPTIVE_ORDER=1`
- **Opt-in `--auto`**: per-role `ROLE_AUTO` and global `ROUTER_AUTO=1` (auto-approves permissions)
- **`--archived`**: passed to `opencode run` when supported (`ROUTER_ARCHIVED=1`)

### Dashboard / control panel (`dashboard/server.py` + `dashboard/index.html` + `dashboard/style.css` + `dashboard/app.js`)
- **Modular frontend**: `index.html` (structure), `style.css` (all styles, dark/light themes), `app.js` (all logic: api, tables, jobs, settings, exports, SSE, confirm dialog)
- **Token authentication**: per-process random API token injected into the served page, required via `X-Router-Token` on every `/api/*` call (constant-time compare via `hmac.compare_digest`); rotates after 1000 requests or via `POST /api/token/regen`; old tokens immediately invalidated
- **Security headers**: CSP with `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`
- **Host/Origin guards**: DNS-rebinding protection, non-loopback Host header rejection, cross-origin POST rejection
- **Rate limiting**: token bucket (30 requests per 60 seconds per IP) on mutating endpoints
- **SSE real-time updates**: `/api/stream` broadcasts state changes; `EventSource` with auto-reconnect, falls back to polling when tab hidden
- **Job management**: bounded history (50), mutate under lock, running jobs cancellable via `POST /api/cancel`
- **Audit log**: `state/audit.jsonl` records action, IP, user agent for every mutating endpoint; `GET /api/audit` returns last 500 entries
- **Settings persistence**: `/api/settings` GET/POST — theme, refresh interval stored server-side; survives token regen
- **Export**: CSV/JSON client-side download for keys, events, jobs tables
- **Custom confirm dialogs**: `<dialog>`-backed modal replaces `confirm()` for destructive actions (reset, save, cancel, token regen)
- **Loading/disabled states**: every action button disables + spinner during `fetch`, preventing double-submit
- **Accessibility**: `aria-live` regions, `aria-describedby` on status badges, visible `:focus-visible`, `<label>` on all inputs, keyboard-operable dialogs, reduced-motion support
- **Responsive**: grid collapses on mobile; tables sticky headers; scrollable panels

### Orchestrator (`scripts/orchestrator.py`)
- **Role-based orchestration**: planner → coder → reviewer → tester → security reviewer, run sequentially
- **Per-role timeouts**: configurable via `ROLE_TIMEOUT_<ROLE>` (planner 180s, coder 900s, etc.)
- **Bounded context carry-over**: `CONTEXT_CHARS` (default 6000) tail of earlier role output passed to later roles, labelled untrusted
- **Opt-in `--auto` per role**: `ROLE_AUTO` env var

### Quality gate (`scripts/quality_gate.py`)
- **Fail-closed**: fails if test command exits non-zero or review evidence file missing
- Runs the supplied test command in the project parent directory

### Optimizer (`scripts/optimizer.py`)
- **Telemetry-based model scoring**: collects stats from `state/events.jsonl`
- **Smoothed scores**: Laplace smoothing; flags low-sample models
- Importable without telemetry (refactored to `collect_stats()` / `main(path)`)

### Key pool (`scripts/key_pool.py`)
- Provider-scoped key slots with cooldowns and success/failure counters
- Rotates on authentication errors only (not quota/rate-limit)

## Requirements
- OpenCode CLI installed and provider credentials configured.
- Python 3.10+, Bash, GNU Make.
- `timeout` is optional; used for per-model command deadlines when installed.

## Install
```bash
unzip opencode-free-router.zip
cd opencode-free-router
make doctor
make check-models
make test
```
Copy the `Makefile`, `scripts/`, `config/`, `dashboard/`, `tests/`, and `state/` directories into your project root if you want the router to operate on that project. The router invokes OpenCode from the current working directory.

## Basic routes
```bash
make plan TASK=code
make code PROMPT='Inspect the repository and fix confirmed bugs.'
make review PROMPT='Review the current implementation.'
make fast PROMPT='Explain this error.'
```

## Orchestrator
```bash
make orchestrate PROMPT='Implement feature X with tests and security review.'
```
Roles run sequentially: planner, coder, reviewer, tester, security reviewer. This is orchestration over model calls, not proof of independent organizational review. For a truly independent reviewer, configure a separate provider/account or external reviewer and preserve its evidence.

## Quality gate
```bash
make quality TEST_COMMAND='pytest -q' INDEPENDENT_REVIEW_FILE='docs/independent-review.md'
```
The gate fails closed if either the test command or independent review evidence is missing. It runs the supplied test command with the shell in the project parent directory. Only pass trusted commands; do not pass untrusted prompt-derived shell strings.

## Dashboard / control panel
See *Control panel* below. `make dashboard` serves http://127.0.0.1:8765, loopback only. Use an authenticated reverse proxy/VPN if remote access is needed; do not expose this unauthenticated server to the public internet.

## Optimizer and circuit state
```bash
make optimize
make health
make reset-circuit
```
Model ranking uses observed success rate and mean latency from `state/events.jsonl`. It is descriptive, not statistically adjusted for task mix or sample size. Edit `config/{code,review,fast,vision,general}.txt` to change candidate order.

## Provider error categories
- `quota_exhausted`
- `rate_limited`
- `authentication`
- `provider_outage`
- `model_unavailable`
- `execution_error`

Classification is heuristic based on OpenCode output. It may misclassify provider-specific messages. The circuit breaker opens after repeated failures (default 2); authentication/model-not-found opens after one failure. Cooldown defaults to 300 seconds. Configure `CIRCUIT_FAILURE_THRESHOLD` and `CIRCUIT_COOLDOWN_SECONDS`.

## Important boundaries
- Catalogue presence does not prove live inference availability or free/unlimited quota.
- Provider error parsing depends on CLI error text; this version does not have provider-native structured error codes.
- The orchestrator stops at the first role that exits non-zero. It does not automatically apply patches based on reviewer output.
- The quality gate checks test command exit status and existence of a review evidence file. It cannot prove the reviewer was independent or that the evidence is truthful.
- The control panel is local-only. It uses a per-process API token against cross-origin/local-process abuse, but there is no user login. It exposes telemetry, never credentials, but can run jobs and edit routes.
- No `--auto` permission bypass is enabled.
- Use Git branches/worktrees and backups for autonomous code changes. Do not run destructive commands against production systems.

## State files
- `state/events.jsonl`: model attempts and latency.
- `state/circuits.json`: per-model breaker state.
- `state/orchestrations/*.json`: role execution status.
- `state/quality-gate.json`: last quality gate result.
- `state/audit.jsonl`: dashboard mutating-action audit log (action, timestamp, IP, user agent).
- `state/dashboard-settings.json`: persisted dashboard settings (theme, refresh interval).
- `state/router.db`: SQLite dual-write of events, audit, circuits, key pools, settings.

## Multi-API-key automatic switching

The router supports ordered key slots per provider. Secrets are read from environment variables only and injected into the OpenCode child process under the provider's configured authentication environment variable. The router never prints or persists key values.

Configure `config/providers.json` with the provider's real auth environment variable and slot variable names. Example for OpenRouter:
```json
{
  "auth_env": "OPENROUTER_API_KEY",
  "key_slots": ["OPENROUTER_API_KEY_1", "OPENROUTER_API_KEY_2", "OPENROUTER_API_KEY_3"]
}
```
Export secrets in your shell or use a local ignored `.keys.env` file based on `config/keys.env.example`. Do not commit `.keys.env`.

```bash
cp config/keys.env.example .keys.env
chmod 600 .keys.env
# Edit .keys.env with actual keys, then:
source .keys.env
make keys
make code PROMPT='Implement the requested change.'
```

On invalid/expired authentication failures, the router cools down that credential slot and tries the next configured authorized key for the same provider. Successful key slots are preferred on subsequent runs. Quota exhaustion and rate limits do NOT trigger account/key hopping: they are treated as provider usage controls, and the router moves to another provider/model instead. `make keys` displays configured slot names and health counters, never secret values. `make key-reset` resets key pool cooldowns/statistics.

### Important provider compatibility notes
- The `auth_env` value must match the environment variable actually consumed by that OpenCode provider integration. Confirm it from the provider's documentation/configuration. The example names are common conventions, not proof that every provider uses them.
- Provider namespace in a model ID is used to select a profile. If a provider is a custom alias, add its mapping to `config/providers.json`.
- This implementation injects one provider key into the child OpenCode process. It does not modify OpenCode's credential store and does not call `opencode auth import/export/switch`.
- Use only keys/accounts you own or are explicitly authorized to administer. Key rotation is only for credential failover (for example, an expired/revoked key). It must not be used to evade quotas, rate limits, billing restrictions, account suspensions, or abuse controls. When quota/rate limits are returned, the router does not switch accounts within that provider.
- Do not use keys from unrelated users. Prefer a secret manager in production. Never paste real keys into chat or commit them.
- OpenCode model catalogue listing is not a live key validation. The first inference is the actual provider test.

## Control panel

Start the local frontend and API:
```bash
make dashboard
# http://127.0.0.1:8765
```

The control panel provides:
- Routed task execution (task, optional model override, model cap, timeout).
- Route preview and editable ordered model candidate lists for code/review/fast/vision/general.
- Model success rate, observed latency, circuit state, and recent attempt history.
- API-key slot presence and cooldown/counters; secret values are never sent to the browser.
- Read-only health and installed model catalogue views.
- Circuit and key-pool state reset controls.
- Recent job output/status with automatic 5-second refresh (SSE with polling fallback when tab hidden).
- Table sorting/filtering/pagination, job detail view with expandable output, CSV/JSON export, settings persistence, custom confirm dialogs, loading states on in-flight actions.
- Dark/light theme with persistence, ARIA live regions, focus-visible styling, keyboard-navigable dialogs.

Security boundaries:
- The server refuses non-loopback binds and non-loopback `Host` headers (DNS-rebinding guard). Do not expose it directly to a LAN or the public internet.
- POST requests validate the browser Origin when present and validate task/model/limit inputs.
- Every `/api/*` call requires `X-Router-Token` (constant-time compare). Token rotates after 1000 requests and via `POST /api/token/regen`; old tokens are immediately invalidated.
- Rate limiting (token bucket, 30 requests per 60 seconds per IP) on mutating endpoints.
- Audit log (`state/audit.jsonl`) records action, IP, and user agent for every mutating endpoint.
- The frontend does not accept or display API key values. Configure keys through the ignored `.keys.env` file or the process environment, then restart the dashboard so its child processes inherit them.
- The dashboard starts router jobs using argument arrays, never shell interpolation. It does not enable OpenCode `--auto`.
- The dashboard is a local administrative surface, not a multi-user authenticated control plane. For remote access, put it behind an authenticated reverse proxy and restrict network access.
- Job records are in-memory and disappear when the dashboard restarts; model attempt telemetry remains in `state/events.jsonl`.

## Tuning knobs
| Variable | Default | Effect |
|---|---|---|
| `ADAPTIVE_ORDER` | `0` | `1` reorders candidates by observed (smoothed) success rate |
| `REQUIRE_ENV_KEY` | `0` | `1` skips providers that have no env key slot instead of using OpenCode's stored credentials |
| `ROUTER_ARCHIVED` | `1` | pass `--archived` to `opencode run` when supported |
| `ROUTER_AUTO` | `0` | `1` passes `--auto` (auto-approve permissions) to `opencode run`. Non-interactive runs cannot answer permission prompts, so file-editing roles may need it; keep off for untrusted prompts |
| `ROLE_AUTO` | empty | comma list of orchestrator roles run with `--auto`, e.g. `coder,tester` |
| `ROLE_TIMEOUT_<ROLE>` | planner 180, coder 900, reviewer 300, tester 600, security_reviewer 300 | per-role timeout seconds (`TIMEOUT_SECONDS`, if set, applies to all roles) |
| `CIRCUIT_QUOTA_COOLDOWN_SECONDS` | `3600` | how long a model stays blocked after an out-of-credits error |
| `CONTEXT_CHARS` | `6000` | prior-role output carried into the next orchestrator role |
| `CIRCUIT_FAILURE_THRESHOLD`, `CIRCUIT_COOLDOWN_SECONDS`, `KEY_COOLDOWN_SECONDS` | `2`, `300`, `300` | breaker / key cooldown behaviour |
| `DASHBOARD_HOST` | `127.0.0.1` | dashboard bind host (loopback only) |
| `DASHBOARD_PORT` | `8765` | dashboard bind port |
| `DASHBOARD_TOKEN_BYTES` | `24` | API token length |
| `LOG_LEVEL` | `INFO` | logging level |
| `OPENCODE_SERVER` | unset | if set, router calls `opencode serve` v2 API (`/api/runs`) instead of CLI subprocess |

`opencode-cli-export/` holds `--help` dumps of the installed OpenCode CLI, used as the reference for flags the router relies on.

## API reference

### Dashboard API (`dashboard/server.py`)
All endpoints require `X-Router-Token` header (constant-time compare) except SSE.

| Endpoint | Method | Description |
|---|---|---|
| `GET /` | GET | Serve control panel HTML with injected token |
| `GET /api/summary` | GET | Telemetry summary: events, models, circuits, keys, jobs |
| `GET /api/health` | GET | Circuit and key-pool state |
| `GET /api/stream` | GET | SSE real-time updates |
| `GET /api/task-config?task=<t>` | GET | Ordered model list for a task |
| `GET /api/catalogue` | GET | Installed model catalogue via `opencode models` |
| `GET /api/audit` | GET | Last 500 audit log entries (action, IP, UA) |
| `GET /api/settings` | GET | Dashboard settings (theme, refresh interval) |
| `POST /api/run` | POST | Queue a router job (202) |
| `POST /api/cancel` | POST | Cancel a running job by ID |
| `POST /api/plan` | POST | Preview route for a task |
| `POST /api/task-config` | POST | Save ordered model list for a task |
| `POST /api/reset-circuits` | POST | Reset circuit breaker state |
| `POST /api/reset-keys` | POST | Reset key-pool cooldowns/counters |
| `POST /api/token/regen` | POST | Regenerate the API token; old tokens invalidated |
| `POST /api/settings` | POST | Save dashboard settings |

### OpenCode v2 API (`opencode serve`)
The router can call `opencode serve`'s HTTP API when `OPENCODE_SERVER` is set (requires `OPENCODE_PASSWORD`):

| Endpoint | Method | Description |
|---|---|---|
| `GET /api/info` | GET | Server info |
| `POST /api/runs` | POST | Run a message (NDJSON event stream) |
| `GET /api/runs/{id}` | GET | Run status |
| `DELETE /api/runs/{id}` | DELETE | Cancel run |
| `GET /api/models` | GET | Available models |
| `GET /api/sessions` | GET | List sessions |

## Documentation
- [AGENTS.md](AGENTS.md) — agent instructions for this repo
- [CLAUDE.md](CLAUDE.md) — Claude Code instructions
- [CODEX.md](CODEX.md) — Codex instructions
- [GEMINI.md](GEMINI.md) — Gemini instructions
- [OPENCODE.md](OPENCODE.md) — OpenCode CLI integration reference
- [CHANGELOG.md](CHANGELOG.md) — release lineage and change history
- [ROADMAP.md](ROADMAP.md) — completed and planned work
- [GOVERNANCE.md](GOVERNANCE.md) — decision-making and versioning
- [CONTRIBUTING.md](CONTRIBUTING.md) — contribution workflow
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — community conduct standards
- [SECURITY.md](SECURITY.md) — security policy and disclosure
- [MASTER.md](MASTER.md) — refactor plan and implementation notes
- [IMPLEMENTATION-CHECKLIST.md](IMPLEMENTATION-CHECKLIST.md) — task tracking
- [config/QUALITY-GATE.md](config/QUALITY-GATE.md) — quality gate details

## Testing
```bash
python -m unittest discover -s tests -v          # 67 tests
python -m py_compile scripts/*.py dashboard/server.py
shellcheck scripts/load-keys.sh
make -n help test keys optimize >/dev/null
```

## License
See repository root for license information.
