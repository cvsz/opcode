# OPENCODE.md

# OpenCode integration

The router wraps the OpenCode CLI (v2.2.7) for model inference.

## `opencode run`

```
opencode run [flags] [<message...>]
```

Key flags used by the router:

| Flag | Purpose |
|---|---|
| `--model, -m` | Model in `provider/model#variant` format |
| `--format` | `default` or `json` (NDJSON event stream) |
| `--auto` | Auto-approve permissions (opt-in via `ROUTER_AUTO=1`) |
| `--archived` | Session stays out of session lists (`ROUTER_ARCHIVED=1`) |
| `--session, -s` | Continue or create a session |
| `--worktree, -w` | New session in a git worktree |
| `--file, -f` | Attach a file |
| `--title` | Session title |
| `--thinking` | Show thinking blocks |

## `opencode serve` — v2 API

The router could migrate from CLI subprocess to the HTTP API:

```
opencode serve --port 49999 --service
```

Endpoints (OpenAPI 3.1, requires `OPENCODE_PASSWORD`):

- `GET /api/info` — server info
- `POST /api/runs` — run a message (NDJSON stream)
- `GET /api/runs/{id}` — run status
- `DELETE /api/runs/{id}` — cancel run
- `GET /api/models` — available models
- `GET /api/sessions` — list sessions
- `GET /api/stats` — usage statistics

## NDJSON event stream (from `--format json`)

Each line is a JSON event:

```json
{"type":"step_start","timestamp":1794419776151,"sessionID":"ses_...","part":{...}}
{"type":"tool_use","part":{"tool":"skill","state":{"status":"completed"}}}
{"type":"text","part":{"text":"Hello"}}
{"type":"step_finish","part":{"reason":"stop","tokens":{...},"cost":0}}
```

The router should migrate `classify()` from CLI text parsing to parsing these structured events.

## Authentication

```bash
opencode auth list       # list integrations and credentials
opencode auth login      # connect an integration
opencode auth export     # print stored credentials as JSON (never called by router)
```
