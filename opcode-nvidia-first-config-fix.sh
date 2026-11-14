#!/usr/bin/env bash
# OPCODE v5.0.0 — safe NVIDIA-first configuration patch.
# Changes provider priority and provider metadata only. Never creates or prints secrets.
set -Eeuo pipefail
umask 077

ROOT="${OPCODE_ROOT:-/mnt/zdata/opcode}"
PROVIDERS="${ROOT}/config/providers.json"
CONFIG_DIR="${HOME}/.config/opcode"
ENV_FILE="${CONFIG_DIR}/config.env"

die() { printf '[opcode-patch] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[opcode-patch] %s\n' "$*" >&2; }

[[ -d "$ROOT" ]] || die "Project directory not found: $ROOT"
[[ -f "$PROVIDERS" ]] || die "Missing provider config: $PROVIDERS"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
command -v jq >/dev/null 2>&1 || die "jq is required"
jq -e 'type == "object" and (.providers | type == "object")' "$PROVIDERS" >/dev/null \
  || die "providers.json has an invalid schema"

mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
cp -a "$PROVIDERS" "${PROVIDERS}.bak.${stamp}"
cp -a "$ENV_FILE" "${ENV_FILE}.bak.${stamp}"

tmp_json="$(mktemp "${PROVIDERS}.XXXXXX")"
tmp_env="$(mktemp "${ENV_FILE}.XXXXXX")"
cleanup() { rm -f "${tmp_json:-}" "${tmp_env:-}"; }
trap cleanup EXIT

python3 - "$PROVIDERS" "$tmp_json" <<'PY'
import json
import sys
from pathlib import Path

src, dst = map(Path, sys.argv[1:])
data = json.loads(src.read_text(encoding="utf-8"))
providers = data.setdefault("providers", {})
if not isinstance(providers, dict):
    raise SystemExit("providers must be a JSON object")

# NVIDIA direct API credentials. Keep key values exclusively in the environment.
providers["nvidia"] = {
    "auth_env": "NVIDIA_API_KEY",
    "key_slots": [f"NVIDIA_API_KEY_{i}" for i in range(1, 9)],
}
data["version"] = max(int(data.get("version", 1)), 1)
security = data.setdefault("security", {})
security["secrets_in_repository"] = False
security["key_sources"] = "environment variables only"
security["never_log_key_values"] = True

dst.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

jq -e '
  .providers.nvidia.auth_env == "NVIDIA_API_KEY" and
  (.providers.nvidia.key_slots | length == 8) and
  .security.never_log_key_values == true
' "$tmp_json" >/dev/null || die "NVIDIA provider config validation failed"

# Idempotently replace the provider order; preserve every other setting and secret.
python3 - "$ENV_FILE" "$tmp_env" <<'PY'
import sys
from pathlib import Path

src, dst = map(Path, sys.argv[1:])
lines = src.read_text(encoding="utf-8").splitlines() if src.exists() else []
key = "OPCODE_PROVIDER_ORDER"
replacement = f"{key}=nvidia,opencode,openrouter,kilo"
out = []
inserted = False
for line in lines:
    if line.strip().startswith(key + "="):
        if not inserted:
            out.append(replacement)
            inserted = True
        continue
    out.append(line)
if not inserted:
    out.append(replacement)
dst.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
PY

chmod 600 "$tmp_json" "$tmp_env"
mv -f "$tmp_json" "$PROVIDERS"
mv -f "$tmp_env" "$ENV_FILE"
trap - EXIT

# Do not print credential values. Detect common supported key layouts only.
configured=0
for name in NVIDIA_API_KEY NVIDIA_API_KEYS NVIDIA_API_KEY_{1..8}; do
  if grep -Eq "^[[:space:]]*${name}=" "$ENV_FILE"; then
    configured=1
  fi
done

log "NVIDIA-first provider order installed."
log "Provider order: nvidia,opencode,openrouter,kilo"
log "Provider config: $PROVIDERS"
log "Runtime config:  $ENV_FILE"
log "Backups:         ${PROVIDERS}.bak.${stamp} and ${ENV_FILE}.bak.${stamp}"
if (( configured )); then
  log "NVIDIA credential variable is present in config.env (value not displayed)."
else
  log "No NVIDIA credential variable found in config.env."
  log "Set NVIDIA_API_KEY or NVIDIA_API_KEY_1..N in your protected environment config."
fi

printf '\nNext checks:\n'
printf '  opcode config show\n'
printf '  opcode status\n'
printf '  opcode route explain --task coding\n'
printf '  opcode models list --free-only | grep -i nvidia\n'
printf '\nNote: this patch does not mark any model verified_free and does not implement a missing provider adapter.\n'
