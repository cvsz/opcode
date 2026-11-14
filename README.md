# OPCODE 5.1.0 — NVIDIA Direct Provider

The canonical executable is `opcode` (5.1.0). `opcode.sh` is the legacy 4.0.2 runner and does not contain the direct NVIDIA adapter; install and invoke `opcode`.

## Requirements
- Bash 4.4+
- `curl`, `jq`, `flock`, `coreutils`
- OpenCode CLI (`opencode`) for non-NVIDIA providers and agentic execution

## Install
```bash
chmod 0755 ./opcode
sudo install -m 0755 ./opcode /usr/local/bin/opcode
mkdir -p ~/.config/opcode/routes
cp config/*.txt ~/.config/opcode/routes/
opcode init
chmod 600 ~/.config/opcode/config.env
```

Edit `~/.config/opcode/config.env` and keep it private (`chmod 600`).

## Import the supplied model catalog
The import command expects one model identifier per line:
```bash
opcode catalog import /path/to/models.txt
```
The supplied `models(1).json` file is a line-oriented identifier list despite its extension, so it can be imported directly.

## Synchronize OpenRouter pricing metadata
```bash
opcode catalog sync-openrouter
```
Only entries whose public catalog reports zero prompt and completion pricing are automatically marked `verified_free`. This is evidence from the catalog, not a guarantee of account entitlement, quota, or live availability.

NVIDIA discovery also records native IDs under the `nvidia/` provider namespace and appends `:free` as a catalog suffix. `/v1/models` is discovery only; it does not prove free pricing or account entitlement. NVIDIA entries remain `unknown` until their free eligibility is independently attested. Other providers remain `unknown` until their own pricing evidence is imported and explicitly attested:
```bash
opcode models mark-free --id 'provider/native-model-id' --evidence 'Provider pricing policy URL or dated account evidence'
```
Do not mark a model free without current, auditable evidence.

## Inspect
```bash
opcode doctor
opcode status
opcode models list --free-only
opcode models verify-free
opcode route explain --task coding
```

## Execute
```bash
opcode run --task coding -- "Review this repository and report findings."
```
Or pin a canonical model:
```bash
opcode run --task coding --model 'nvidia/deepseek-ai/deepseek-v4.1-flash:free' -- "Analyze this code."
```

## NVIDIA Build Free Endpoint Catalog

NVIDIA Build (`build.nvidia.com`) is the authoritative source for NVIDIA-hosted Free Endpoint models.
The integration discovers models via RSC flight-data parsing with pagination, classifies endpoint
types, and resolves NIM API model IDs from official `.md` detail pages.

Only models explicitly labelled `Free Endpoint` via the catalog `nimType` label are eligible for
free inference routing. `Download Available` and `Partner Endpoint` are kept distinct and are
excluded from free-only routing. Free eligibility is never inferred from `/v1/models`, model names,
`:free` suffixes, or preview status.

```bash
# Discover + classify Build Free Endpoint models (eligibility stays "unknown")
opcode catalog sync-nvidia-build

# Explicitly verify Free Endpoint eligibility -> verified_free
opcode catalog verify-nvidia

# List verified-free NVIDIA models
opcode models list --provider nvidia --free-only

# Route explain shows NVIDIA models first when eligible for the task
opcode route explain --task coding
```

Catalog snapshots preserve provenance, SHA-256 hashes, verification timestamps, and change
detection (deprecated / removed / unavailable / changed models are flagged, never silently
promoted). Catalog sync never silently upgrades `unknown` models to `verified_free` — that is a
separate, explicit verification step.

For NVIDIA Direct NIM inference against `NVIDIA_BASE_URL`:
```bash
opcode catalog sync-nvidia
opcode models list --provider nvidia
```

## NVIDIA Direct Provider (legacy NIM inventory)

Synchronize the NVIDIA NIM model inventory:
```bash
opcode catalog sync-nvidia
opcode models list --provider nvidia
```

For NVIDIA models, OPCODE keeps three distinct identifiers:
- **Catalog:** `nvidia/<publisher>/<model>:free`
- **OPCODE runtime:** `nvidia/<publisher>/<model>` (the `:free` suffix is removed)
- **NVIDIA API payload:** `<publisher>/<model>` (the provider namespace is removed because NVIDIA `/v1/models` returns native publisher/model IDs)

`opcode run` sends NVIDIA requests directly to `NVIDIA_BASE_URL/chat/completions`; it does not invoke `opencode run` for NVIDIA. The direct adapter is text-inference only and does not provide filesystem, shell, or agent tools. `agent_execution` therefore uses the OpenCode route, not NVIDIA Direct. NVIDIA models published under `nvidia/nvidia/*` rank first, followed by other NVIDIA publishers, then the configured fallback providers. The exact direct NVIDIA route allowlist is maintained in `config/nvidia-free-models.txt`; task-specific ordering lives in `config/{code,general,review,fast,vision,agent_execution}.txt`. Missing allowlist files fail closed for NVIDIA models.

Configure credentials in the protected config or environment:
```bash
NVIDIA_API_KEY=nvapi-...       # single key
# or NVIDIA_API_KEYS=key1,key2  # comma-separated rotation pool
# or NVIDIA_API_KEY_1=...       # numbered key slots
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_RPM=35
NVIDIA_TIMEOUT=90
NVIDIA_MAX_TOKENS=1024
```

Only authentication failures rotate to another NVIDIA key. Model rejection and rate limits cool down that model and move to the next eligible candidate. Timeouts, transport failures, and server errors stop rather than replaying a potentially billable inference request.

## Important runtime boundary
- NVIDIA requests use the direct NVIDIA NIM chat-completions adapter; non-NVIDIA requests use `opencode run --model <canonical-id> <prompt>`.
- Provider keys are read from environment variables only and are never written to the registry or logs.
- Automatic fallback is deliberately limited to clearly classified admission errors (model unavailable, authentication/permission rejection, rate limit, quota). Ambiguous errors stop execution to avoid replaying agent side effects.
- The initial catalog import does not prove pricing, compatibility, or availability.
- NVIDIA catalog discovery is implemented against `/v1/models`; discovery does not certify free eligibility. Kilo pricing/eligibility remains provider-specific and requires separate verification.
- Review this script against your installed OpenCode CLI version before production rollout.
