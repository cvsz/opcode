#!/usr/bin/env bash
# OPCODE v5.x — configure NVIDIA as the first provider, safely and idempotently.
# This changes provider metadata and routing priority only; it does not create
# credentials, add an API adapter, or certify any model as free.
set -Eeuo pipefail
umask 077

ROOT="${OPCODE_ROOT:-/mnt/zdata/opcode}"
PROVIDERS="${ROOT}/config/providers.json"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/opcode"
ENV_FILE="${OPCODE_CONFIG_FILE:-${CONFIG_DIR}/config.env}"

log() { printf '[opcode-nvidia-first] %s\n' "$*"; }
die() { printf '[opcode-nvidia-first] ERROR: %s\n' "$*" >&2; exit 1; }

[[ -d "$ROOT" ]] || die "Project directory not found: $ROOT (override with OPCODE_ROOT)"
[[ -f "$PROVIDERS" ]] || die "Missing provider registry: $PROVIDERS"
command -v python3 >/dev/null 2>&1 || die 'python3 is required'
command -v jq >/dev/null 2>&1 || die 'jq is required'
jq -e 'type == "object" and (.providers | type == "object")' "$PROVIDERS" >/dev/null \
  || die 'providers.json does not contain an object at .providers'

mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"
if [[ -e "$ENV_FILE" && ! -f "$ENV_FILE" ]]; then
  die "Config path exists but is not a regular file: $ENV_FILE"
fi
: >> "$ENV_FILE"
chmod 600 "$ENV_FILE"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_json="${PROVIDERS}.bak.${stamp}"
backup_env="${ENV_FILE}.bak.${stamp}"
cp -p -- "$PROVIDERS" "$backup_json"
cp -p -- "$ENV_FILE" "$backup_env"

tmp_json="$(mktemp "${PROVIDERS}.tmp.XXXXXX")"
tmp_env="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
cleanup() { rm -f -- "${tmp_json:-}" "${tmp_env:-}"; }
trap cleanup EXIT

python3 - "$PROVIDERS" "$tmp_json" <<'PY'
import json
import sys
from pathlib import Path

source, target = map(Path, sys.argv[1:])
data = json.loads(source.read_text(encoding="utf-8"))
providers = data.get("providers")
if not isinstance(providers, dict):
    raise SystemExit("Invalid schema: .providers must be an object")

# Preserve unrelated provider definitions and metadata.
providers["nvidia"] = {
    "auth_env": "NVIDIA_API_KEY",
    "key_slots": [f"NVIDIA_API_KEY_{index}" for index in range(1, 9)],
}
security = data.setdefault("security", {})
if not isinstance(security, dict):
    raise SystemExit("Invalid schema: .security must be an object")
security["secrets_in_repository"] = False
security["key_sources"] = "environment variables only"
security["never_log_key_values"] = True

target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

python3 - "$ENV_FILE" "$tmp_env" <<'PY'
import sys
from pathlib import Path

source, target = map(Path, sys.argv[1:])
lines = source.read_text(encoding="utf-8").splitlines()
key = "OPCODE_PROVIDER_ORDER"
value = "nvidia,opencode,openrouter,kilo"
output: list[str] = []
inserted = False
for line in lines:
    stripped = line.strip()
    # Replace active assignments only; retain comments and unrelated settings.
    if stripped.startswith(key + "="):
        if not inserted:
            output.append(f"{key}={value}")
            inserted = True
        continue
    output.append(line)
if not inserted:
    output.append(f"{key}={value}")
target.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
PY

jq -e '
  (.providers.nvidia.auth_env == "NVIDIA_API_KEY") and
  (.providers.nvidia.key_slots | length == 8) and
  (.security.never_log_key_values == true)
' "$tmp_json" >/dev/null || die 'NVIDIA provider metadata validation failed'
grep -Fxq 'OPCODE_PROVIDER_ORDER=nvidia,opencode,openrouter,kilo' "$tmp_env" \
  || die 'Provider-order validation failed'

chmod 600 "$tmp_json" "$tmp_env"
mv -f -- "$tmp_json" "$PROVIDERS"
mv -f -- "$tmp_env" "$ENV_FILE"
trap - EXIT

log 'Configuration updated successfully.'
log 'Provider priority: nvidia,opencode,openrouter,kilo'
log "Provider metadata: $PROVIDERS"
log "Runtime config: $ENV_FILE"
log "Backups: $backup_json ; $backup_env"

if grep -Eq '^[[:space:]]*(export[[:space:]]+)?NVIDIA_API_KEY(_[1-8]|S)?=' "$ENV_FILE"; then
  log 'An NVIDIA credential assignment exists in config.env; its value was not read or displayed.'
else
  log 'No NVIDIA credential assignment found in config.env.'
  log 'Provide NVIDIA_API_KEY or NVIDIA_API_KEY_1 through NVIDIA_API_KEY_8 in the protected runtime environment.'
fi

cat <<'EOF'

Validation commands:
  opcode config show
  opcode status
  opcode route explain --task coding

Interpretation:
  - NVIDIA is first in configured provider priority.
  - Direct NVIDIA routing works only if this OPCODE build has a functioning
    NVIDIA adapter and a valid NVIDIA API credential.
  - openrouter/nvidia/* models are still routed through OpenRouter, not directly
    through NVIDIA.
  - This script does not label models verified_free. Verify current NVIDIA
    pricing, free-tier eligibility, quotas, and model IDs independently.
EOF
