#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
FILE="${1:-$ROOT/.keys.env}"
[[ -f "$FILE" ]] || { echo "Missing key file: $FILE" >&2; exit 1; }
chmod 600 "$FILE"
set -a
# shellcheck disable=SC1090
source "$FILE"
set +a
exec "$@"
