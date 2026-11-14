#!/usr/bin/env bash
# opcode — single-file OpenCode runner with live, fail-closed free-model discovery.
# Requires: Bash 4+, Python 3.9+, curl, and OpenCode CLI.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

VERSION="4.0.2"
CALLER_DIR="$(pwd -P)"
TARGET_DIR="$CALLER_DIR"
ENV_FILE=""
MODELS_FILE="${MODELS_JSON_PATH:-}"
CACHE_FILE="${XDG_CACHE_HOME:-$HOME/.cache}/opcode/free-models.json"
PROMPT_LANG="${PROMPT_LANG:-en}"
MAX_ATTEMPTS="${MAX_RETRY_ATTEMPTS:-12}"
SYNC_API=1
DRY_RUN=0
SYNC_ONLY=0
CUSTOM_PROMPT=""
declare -a API_KEYS=()
declare -a MODELS=()

log()  { printf '[opcode] %s\n' "$*" >&2; }
die()  { log "ERROR: $*"; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"; }

usage() {
  cat <<'HELP'
opcode — live free-model OpenCode runner

Usage:
  opcode [options] [PROJECT_DIR] [-- PROMPT...]
  opcode --dir /path/to/project --api-key KEY1 --api-key KEY2
  opcode --sync-only
  opcode --list-models

Options:
  -d, --dir PATH            Project working directory (must already exist)
  -e, --env-file PATH       Read KEY=VALUE entries from this file (no shell execution)
  -k, --api-key KEY         Add an OpenRouter API key; repeat or comma-separate
      --models-file PATH    Write the synchronized free model IDs to PATH
      --cache-file PATH     Set the internal JSON cache path
      --max-attempts N      Maximum model/key attempts (default: 12)
      --lang th|en          Built-in task prompt language (default: en)
  -p, --prompt TEXT         Use a custom prompt instead of the built-in prompt
      --no-sync             Use the last successfully synchronized cache
      --sync-only           Synchronize catalog and exit
      --list-models         Print synchronized free model IDs and exit
      --dry-run             Sync and show the plan without invoking OpenCode
      -h, --help                Show this help
      --version             Show version

Credentials:
  OPENROUTER_API_KEY=...
  OPENROUTER_API_KEYS="key1,key2"
  OPENROUTER_API_KEY_1=...
  OPENROUTER_API_KEY_2=...

Only models returned by OpenRouter's public /models endpoint with both prompt
and completion pricing exactly zero are admitted. Paid models are never used.
The API key is supplied to each OpenCode process through OPENROUTER_API_KEY.
Keys are rotated on rate-limit, quota, authentication, and transient failures.
Keys are never printed or written to the model catalog.

Examples:
  opcode -d ./my-project -k "$OPENROUTER_API_KEY"
  opcode --dir . --api-key keyA,keyB --max-attempts 20
  opcode --sync-only
  opcode --list-models
HELP
}

trim() {
  local v="$1"
  v="${v#"${v%%[![:space:]]*}"}"
  v="${v%"${v##*[![:space:]]}"}"
  printf '%s' "$v"
}

resolve_path() {
  local p="$1" base="$2"
  [[ "$p" = /* ]] && { printf '%s\n' "$p"; return; }
  printf '%s/%s\n' "$base" "$p"
}

# Parse a deliberately small dotenv subset. Do not source arbitrary shell code.
load_env_file() {
  local file="$1" line key value
  [[ -f "$file" ]] || die "Environment file not found: $file"
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="$(trim "$line")"
    [[ -z "$line" || "$line" == \#* ]] && continue
    line="${line#export }"
    [[ "$line" == *=* ]] || continue
    key="$(trim "${line%%=*}")"
    value="$(trim "${line#*=}")"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if [[ ${#value} -ge 2 ]]; then
      if [[ "$value" == \"*\" ]]; then value="${value:1:${#value}-2}"
      elif [[ "$value" == \'*\' ]]; then value="${value:1:${#value}-2}"
      fi
    fi
    case "$key" in
      OPENROUTER_API_KEY|OPENROUTER_API_KEYS|OPENROUTER_API_KEY_[0-9]*)
        printf -v "$key" '%s' "$value"; export "$key" ;;
    esac
  done < "$file"
}

add_key() {
  local candidate="$1" existing
  candidate="$(trim "$candidate")"
  [[ -n "$candidate" ]] || return 0
  [[ "$candidate" != *$'\n'* && "$candidate" != *$'\r'* ]] || die "Invalid API key value"
  for existing in "${API_KEYS[@]:-}"; do
    [[ "$existing" == "$candidate" ]] && return 0
  done
  API_KEYS+=("$candidate")
}

collect_keys() {
  local raw name value
  raw="${OPENROUTER_API_KEY:-}"
  [[ -n "$raw" ]] && { IFS=',' read -r -a _parts <<< "$raw"; for value in "${_parts[@]}"; do add_key "$value"; done; }
  raw="${OPENROUTER_API_KEYS:-}"
  [[ -n "$raw" ]] && { IFS=',' read -r -a _parts <<< "$raw"; for value in "${_parts[@]}"; do add_key "$value"; done; }
  while IFS= read -r name; do
    [[ "$name" =~ ^OPENROUTER_API_KEY_[0-9]+$ ]] || continue
    add_key "${!name}"
  done < <(compgen -A variable | sort -V)
}

sync_models() {
  local destination="$1"
  mkdir -p "$(dirname "$destination")"
  local tmp
  tmp="$(mktemp "${destination}.XXXXXX")"
  if ! curl --fail --silent --show-error --location \
      --connect-timeout 8 --max-time 35 \
      -H 'Accept: application/json' \
      'https://openrouter.ai/api/v1/models' -o "$tmp"; then
    rm -f "$tmp"
    return 1
  fi
  if ! python3 - "$tmp" "$destination" <<'PY'
import json, os, re, sys, tempfile
source, destination = sys.argv[1:]
try:
    with open(source, encoding="utf-8") as f:
        payload = json.load(f)
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("API response has no data array")
    models = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        pricing = item.get("pricing")
        if not isinstance(model_id, str) or not re.fullmatch(r"[A-Za-z0-9._:/+-]+", model_id):
            continue
        if not isinstance(pricing, dict):
            continue
        # OpenRouter publishes per-token prices as decimal strings.
        try:
            prompt = float(pricing.get("prompt", "nan"))
            completion = float(pricing.get("completion", "nan"))
        except (TypeError, ValueError):
            continue
        if prompt != 0.0 or completion != 0.0:
            continue
        if model_id not in seen:
            seen.add(model_id)
            models.append({
                "id": model_id,
                "name": item.get("name") if isinstance(item.get("name"), str) else model_id,
                "context_length": item.get("context_length"),
                "pricing": {"prompt": "0", "completion": "0"},
            })
    if not models:
        raise ValueError("API returned zero models verified as free; refusing to overwrite cache")
    models.sort(key=lambda x: x["id"].casefold())
    fd, temp = tempfile.mkstemp(prefix=".free-models.", dir=os.path.dirname(os.path.abspath(destination)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"source": "https://openrouter.ai/api/v1/models",
                       "verified_free_only": True, "models": models}, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, destination)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    print(len(models))
except Exception as exc:
    print(f"catalog validation failed: {exc}", file=sys.stderr)
    sys.exit(1)
PY
  then
    rm -f "$tmp"
    return 1
  fi
  rm -f "$tmp"
}

read_models() {
  local file="$1" tmp
  tmp="$(mktemp)"
  if ! python3 - "$file" > "$tmp" <<'PY'
import json, sys
from decimal import Decimal, InvalidOperation
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        obj = json.load(f)
    if obj.get("verified_free_only") is not True:
        raise ValueError("cache is not marked verified_free_only")
    if not isinstance(obj.get("models"), list) or not obj["models"]:
        raise ValueError("cache contains no models")
    seen = set()
    for item in obj["models"]:
        if not isinstance(item, dict):
            raise ValueError("model entry is not an object")
        model = item.get("id")
        pricing = item.get("pricing")
        if not isinstance(model, str) or not model:
            raise ValueError("model entry has no valid id")
        if not isinstance(pricing, dict):
            raise ValueError(f"model {model} has no pricing proof")
        try:
            prompt = Decimal(str(pricing.get("prompt")))
            completion = Decimal(str(pricing.get("completion")))
        except (InvalidOperation, TypeError):
            raise ValueError(f"model {model} has invalid pricing")
        if not prompt.is_finite() or not completion.is_finite() or prompt != 0 or completion != 0:
            raise ValueError(f"model {model} is not free; refusing paid-model execution")
        if model not in seen:
            seen.add(model)
            print(model)
except Exception as e:
    print(f"invalid model cache: {e}", file=sys.stderr)
    sys.exit(1)
PY
  then
    rm -f "$tmp"
    die "Model catalog failed strict free-only validation"
  fi
  mapfile -t MODELS < "$tmp"
  rm -f "$tmp"
  ((${#MODELS[@]} > 0)) || die "No verified free models in catalog"
}

is_retryable_output() {
  local output="${1,,}"
  # Only retry explicit provider/model admission failures. Generic timeouts,
  # resets, and server errors may occur after agent execution has begun.
  [[ "$output" =~ (model[[:space:]_-]+unavailable|model[[:space:]_-]+not[[:space:]_-]+found|no[[:space:]_-]+endpoints|rate.?limit|too[[:space:]_-]+many[[:space:]_-]+requests|insufficient_quota|quota[[:space:]_-]+exceeded|payment[[:space:]_-]+required|insufficient[[:space:]_-]+credits|invalid[._[:space:]_-]+api[._[:space:]_-]+key|authentication[[:space:]_-]+failed|unauthorized|forbidden|http[[:space:]_-]*(401|402|403|404|408|409|422|425|429)) ]]
}

builtin_prompt() {
  if [[ "$PROMPT_LANG" == "th" ]]; then
    cat <<'PROMPT'
ทำหน้าที่เป็น Principal Engineer และผู้ตรวจสอบอิสระของโปรเจกต์นี้
1. สำรวจโครงสร้าง โค้ด เอกสาร การตั้งค่า และคำสั่งทดสอบก่อนแก้ไข
2. ระบุข้อบกพร่อง ความเสี่ยงด้านความปลอดภัย งานที่ยังไม่สมบูรณ์ และ regression
3. ลงมือแก้ไขจริงอย่างเป็นลำดับ โดยรักษา backward compatibility และแนวทางสถาปัตยกรรมเดิม
4. เพิ่มหรือปรับปรุง tests และรัน validation ที่เหมาะสม ห้ามอ้างว่าผ่านหากไม่ได้รัน
5. สรุปไฟล์ที่เปลี่ยน เหตุผล ผลทดสอบ ความเสี่ยง และงานที่ถูก BLOCKED
ข้อกำหนด: เปลี่ยนเท่าที่จำเป็น, ไม่สร้าง placeholder, ไม่เปิดเผย secrets, ไม่ลบข้อมูลผู้ใช้,
ไม่รัน destructive operation โดยไม่มีการยืนยัน และห้ามรายงานว่างานเสร็จหากไม่มีหลักฐาน
PROMPT
  else
    cat <<'PROMPT'
Act as the principal engineer and independent reviewer for this project.
1. Inspect repository instructions, architecture, source, configuration, documentation, and tests before editing.
2. Identify defects, security risks, incomplete behavior, and regression risks.
3. Implement the highest-value safe fixes while preserving established architecture and compatibility.
4. Add or update tests and run the repository's real validation commands. Never claim a check passed unless it ran.
5. Report changed files, rationale, test evidence, residual risks, and blocked work.
Constraints: minimal necessary changes; no placeholders; protect secrets and user data; no destructive
operations without authorization; do not claim completion without verifiable evidence.
PROMPT
  fi
}

# Friendly subcommands; preserve the existing flag interface.
case "${1:-}" in
  help) usage; exit 0 ;;
  version) printf 'opcode %s\n' "$VERSION"; exit 0 ;;
  models) shift; set -- --list-models "$@" ;;
  sync) shift; set -- --sync-only "$@" ;;
esac

# CLI parsing
while (($#)); do
  case "$1" in
    -d|--dir|-C) (($# >= 2)) || die "$1 requires a path"; TARGET_DIR="$2"; shift 2 ;;
    -e|--env-file) (($# >= 2)) || die "$1 requires a path"; ENV_FILE="$2"; shift 2 ;;
    -k|--api-key) (($# >= 2)) || die "$1 requires a key"; IFS=',' read -r -a _parts <<< "$2"; for _key in "${_parts[@]}"; do add_key "$_key"; done; shift 2 ;;
    --models-file) (($# >= 2)) || die "$1 requires a path"; MODELS_FILE="$2"; shift 2 ;;
    --cache-file) (($# >= 2)) || die "$1 requires a path"; CACHE_FILE="$2"; shift 2 ;;
    --max-attempts) (($# >= 2)) || die "$1 requires a number"; MAX_ATTEMPTS="$2"; shift 2 ;;
    --lang) (($# >= 2)) || die "$1 requires th or en"; PROMPT_LANG="$2"; shift 2 ;;
    -p|--prompt) (($# >= 2)) || die "$1 requires text"; CUSTOM_PROMPT="$2"; shift 2 ;;
    --no-sync) SYNC_API=0; shift ;;
    --sync-only) SYNC_ONLY=1; shift ;;
    --list-models) SYNC_ONLY=2; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --version) printf 'opcode %s\n' "$VERSION"; exit 0 ;;
    -h|--help) usage; exit 0 ;;
    --) shift; CUSTOM_PROMPT="${*}"; break ;;
    -*) die "Unknown option: $1 (use --help)" ;;
    *)
      if [[ "$TARGET_DIR" == "$CALLER_DIR" ]]; then TARGET_DIR="$1"; shift
      else die "Unexpected positional argument: $1"; fi ;;
  esac
done

[[ "$PROMPT_LANG" == th || "$PROMPT_LANG" == en ]] || die "--lang must be th or en"
[[ "$MAX_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || die "--max-attempts must be a positive integer"
((MAX_ATTEMPTS <= 500)) || die "--max-attempts cannot exceed 500"

TARGET_DIR="$(resolve_path "$TARGET_DIR" "$CALLER_DIR")"
[[ -d "$TARGET_DIR" ]] || die "Project directory does not exist: $TARGET_DIR"
TARGET_DIR="$(cd "$TARGET_DIR" && pwd -P)"

if [[ -n "$ENV_FILE" ]]; then
  ENV_FILE="$(resolve_path "$ENV_FILE" "$CALLER_DIR")"
  load_env_file "$ENV_FILE"
elif [[ -f "$TARGET_DIR/.env" ]]; then
  load_env_file "$TARGET_DIR/.env"
fi
collect_keys

need python3
need curl
if (( SYNC_API )); then
  log "Syncing free models from OpenRouter API..."
  if ! count="$(sync_models "$CACHE_FILE")"; then
    die "Live model synchronization failed. No agent run was started; use --no-sync only if you intentionally trust a prior cache."
  fi
  log "Verified $count free models."
else
  [[ -s "$CACHE_FILE" ]] || die "No cached catalog. Run without --no-sync first."
  log "Using previously verified catalog (live sync disabled)."
fi

read_models "$CACHE_FILE"
if [[ -n "$MODELS_FILE" ]]; then
  MODELS_FILE="$(resolve_path "$MODELS_FILE" "$CALLER_DIR")"
  mkdir -p "$(dirname "$MODELS_FILE")"
  tmp="$(mktemp "${MODELS_FILE}.XXXXXX")"
  printf '%s\n' "${MODELS[@]}" > "$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" "$MODELS_FILE"
fi

if (( SYNC_ONLY )); then
  if (( SYNC_ONLY == 2 )); then printf '%s\n' "${MODELS[@]}"; else log "Catalog synchronized: $CACHE_FILE"; fi
  exit 0
fi
(( DRY_RUN )) && {
  printf 'Project: %s\nCatalog: %s\nModels: %s\nAPI keys: %s\n' "$TARGET_DIR" "$CACHE_FILE" "${#MODELS[@]}" "${#API_KEYS[@]}"
  printf '  %s\n' "${MODELS[@]}"
  exit 0
}

need opencode
((${#API_KEYS[@]} > 0)) || die "No OpenRouter API keys found. Set OPENROUTER_API_KEY(S), use --api-key, or provide --env-file."
PROMPT="$CUSTOM_PROMPT"
[[ -n "$PROMPT" ]] || PROMPT="$(builtin_prompt)"
cd "$TARGET_DIR"

# Build deterministic model/key attempts, cycling through every verified-free model.
attempt=0
success=0
key_count=${#API_KEYS[@]}
model_count=${#MODELS[@]}
while (( attempt < MAX_ATTEMPTS )); do
  model_index=$(( (attempt / key_count) % model_count ))
  key_index=$(( attempt % key_count ))
  model="${MODELS[$model_index]}"
  key="${API_KEYS[$key_index]}"
  attempt=$((attempt + 1))
  log "Attempt $attempt/$MAX_ATTEMPTS — model=$model key-index=$((key_index + 1))/$key_count"
  output_file="$(mktemp)"
  set +e
  opencode_model="$model"
  [[ "$opencode_model" == openrouter/* ]] || opencode_model="openrouter/${opencode_model}"
  OPENROUTER_API_KEY="$key" opencode run --auto --agent build -m "$opencode_model" "$PROMPT" >"$output_file" 2>&1
  status=$?
  set -e
  cat "$output_file"
  if (( status == 0 )); then
    success=1
    rm -f "$output_file"
    log "Completed successfully with $model."
    break
  fi
  if ! is_retryable_output "$(cat "$output_file")"; then
    rm -f "$output_file"
    die "OpenCode failed with a non-retryable error (exit=$status); stopping to avoid masking project errors."
  fi
  rm -f "$output_file"
  log "Provider/model admission failure; trying the next distinct model/key pair."
done

(( success )) || die "All $attempt attempts failed. No paid-model fallback was attempted."
