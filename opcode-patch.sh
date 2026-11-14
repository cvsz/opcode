#!/usr/bin/env bash
# OPCODE v4.0.2 — safe, single-file patcher for the existing OPCODE runner.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

VERSION="4.0.2"
SOURCE="${OPCODE_SOURCE:-}"
INSTALL_PATH="${OPCODE_INSTALL_PATH:-$HOME/.local/bin/opcode}"
BACKUP_SUFFIX=".bak.$(date -u +%Y%m%dT%H%M%SZ)"

log() { printf '[opcode-patch] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"; }

usage() {
  cat <<'USAGE'
OPCODE v4.0.2 patcher

Usage:
  bash opcode-v4.0.2-patch.sh [--source PATH] [--install PATH]

Options:
  --source PATH   Existing opcode.sh source (auto-detected if omitted)
  --install PATH  Destination executable (default: ~/.local/bin/opcode)
  -h, --help      Show this help

Creates a timestamped backup, patches known v4.0.0/v4.0.1 source anchors,
runs syntax and CLI smoke tests, then installs the patched script.
USAGE
}

while (($#)); do
  case "$1" in
    --source) (($# >= 2)) || die "--source requires a path"; SOURCE="$2"; shift 2 ;;
    --install) (($# >= 2)) || die "--install requires a path"; INSTALL_PATH="$2"; shift 2 ;;
    -h|--help|help) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

need python3
need bash
need install
need mktemp

if [[ -z "$SOURCE" ]]; then
  for candidate in "$PWD/opcode.sh" "$HOME/zdata/opcode/opcode.sh" "$HOME/zksato/opcode.sh"; do
    if [[ -f "$candidate" ]]; then SOURCE="$candidate"; break; fi
  done
fi
[[ -n "$SOURCE" && -f "$SOURCE" ]] || die "Cannot locate opcode.sh. Pass --source /absolute/path/to/opcode.sh"
SOURCE="$(cd "$(dirname "$SOURCE")" && pwd -P)/$(basename "$SOURCE")"
[[ -w "$SOURCE" ]] || die "Source is not writable: $SOURCE"

backup="${SOURCE}${BACKUP_SUFFIX}"
cp -p -- "$SOURCE" "$backup"
log "Backup created: $backup"

python3 - "$SOURCE" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

if 'VERSION="4.0.2"' in text:
    raise SystemExit("Source already declares VERSION=4.0.2; refusing duplicate patch.")
if not re.search(r'^VERSION="4\.0\.[01]"$', text, re.M):
    raise SystemExit('Unsupported source version. Expected VERSION="4.0.0" or "4.0.1".')

text, n = re.subn(r'^VERSION="4\.0\.[01]"$', 'VERSION="4.0.2"', text, count=1, flags=re.M)
if n != 1:
    raise SystemExit("Could not safely update VERSION.")

parser_anchor = "# CLI parsing\nwhile (($#)); do"
subcommands = '''# Friendly subcommands; preserve the existing flag interface.
case "${1:-}" in
  help) usage; exit 0 ;;
  version) printf 'opcode %s\\n' "$VERSION"; exit 0 ;;
  models) shift; set -- --list-models "$@" ;;
  sync) shift; set -- --sync-only "$@" ;;
esac

# CLI parsing
while (($#)); do'''
if text.count(parser_anchor) != 1:
    raise SystemExit("Expected exactly one CLI parser anchor.")
text = text.replace(parser_anchor, subcommands, 1)

pattern = re.compile(r'is_retryable_output\(\) \{\n.*?\n\}', re.S)
replacement = r'''is_retryable_output() {
  local output="${1,,}"
  # Only retry explicit provider/model admission failures. Generic timeouts,
  # resets, and server errors may occur after agent execution has begun.
  [[ "$output" =~ (model[[:space:]_-]+unavailable|model[[:space:]_-]+not[[:space:]_-]+found|no[[:space:]_-]+endpoints|rate.?limit|too[[:space:]_-]+many[[:space:]_-]+requests|insufficient_quota|quota[[:space:]_-]+exceeded|payment[[:space:]_-]+required|insufficient[[:space:]_-]+credits|invalid[._[:space:]_-]+api[._[:space:]_-]+key|authentication[[:space:]_-]+failed|unauthorized|forbidden|http[[:space:]_-]*(401|402|403|404|408|409|422|425|429)) ]]
}'''
text, n = pattern.subn(lambda _: replacement, text, count=1)
if n != 1:
    raise SystemExit("Could not safely replace is_retryable_output().")

old_call = 'OPENROUTER_API_KEY="$key" opencode run --auto --agent build -m "$model" "$PROMPT" >"$output_file" 2>&1'
new_call = '''opencode_model="$model"
  [[ "$opencode_model" == openrouter/* ]] || opencode_model="openrouter/${opencode_model}"
  OPENROUTER_API_KEY="$key" opencode run --auto --agent build -m "$opencode_model" "$PROMPT" >"$output_file" 2>&1'''
if text.count(old_call) != 1:
    raise SystemExit("Expected exactly one OpenCode invocation anchor.")
text = text.replace(old_call, new_call, 1)

old_pair = '''  model="${MODELS[$((attempt % model_count))]}"
  key_index=$(( attempt % key_count ))
  key="${API_KEYS[$key_index]}"'''
new_pair = '''  model_index=$(( (attempt / key_count) % model_count ))
  key_index=$(( attempt % key_count ))
  model="${MODELS[$model_index]}"
  key="${API_KEYS[$key_index]}"'''
if text.count(old_pair) != 1:
    raise SystemExit("Expected exactly one model/key rotation anchor.")
text = text.replace(old_pair, new_pair, 1)

text = text.replace(
    'log "Retryable provider/model failure; switching to the next free model/key."',
    'log "Provider/model admission failure; trying the next distinct model/key pair."'
)

path.write_text(text, encoding="utf-8")
PY

chmod 0755 "$SOURCE"
bash -n "$SOURCE" || die "Bash syntax validation failed; backup retained at $backup"

version_output="$("$SOURCE" version 2>&1)" || die "version subcommand failed"
[[ "$version_output" == "opcode $VERSION" ]] || die "Unexpected version output: $version_output"
flag_version="$("$SOURCE" --version 2>&1)" || die "--version failed"
[[ "$flag_version" == "opcode $VERSION" ]] || die "Unexpected --version output: $flag_version"
"$SOURCE" help >/dev/null || die "help subcommand failed"
"$SOURCE" --help >/dev/null || die "--help failed"

install_dir="$(dirname "$INSTALL_PATH")"
mkdir -p "$install_dir"
tmp_install="$(mktemp "${install_dir}/.opcode.XXXXXX")"
trap 'rm -f "${tmp_install:-}"' EXIT
install -m 0755 "$SOURCE" "$tmp_install"
mv -f -- "$tmp_install" "$INSTALL_PATH"
trap - EXIT

installed_version="$("$INSTALL_PATH" version 2>&1)" || die "Installed CLI smoke test failed"
[[ "$installed_version" == "opcode $VERSION" ]] || die "Installed version mismatch: $installed_version"

log "Patch applied and installed successfully."
printf 'Version:       %s\n' "$VERSION"
printf 'Source:        %s\n' "$SOURCE"
printf 'Installed:     %s\n' "$INSTALL_PATH"
printf 'Backup:        %s\n' "$backup"
printf 'Smoke tests:   bash -n, version, --version, help, --help (PASS)\n'
printf '\nNext: opcode sync && opcode models\n'
