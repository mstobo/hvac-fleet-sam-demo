#!/usr/bin/env bash
# Set fleet sketch style (nl | jargon) without recreating containers.
# Writes the shared override file on the dbdata volume (same path chart-query uses).
#
# Usage:
#   ./deploy/aws/scripts/set-sketch-style.sh jargon deploy/aws/.env
#   ./deploy/aws/scripts/set-sketch-style.sh nl
#
# Optional: POST via chart-query if CHART_BASE is set (DASHBOARD_PUBLIC_HOST in .env):
#   curl -sS -X POST "$CHART_BASE/admin/sketch-style" -H 'Content-Type: application/json' -d '{"style":"jargon"}'
#
set -euo pipefail

STYLE="${1:-}"
ENV_FILE="${2:-deploy/aws/.env}"

if [[ -z "${STYLE}" ]]; then
  echo "Usage: $0 <nl|jargon> [env-file]" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "${ROOT}"

DATA_DIR="./data"
if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  DATA_DIR="${DATA_DIR:-./data}"
fi

OVERRIDE_PATH="${SKETCH_STYLE_OVERRIDE_PATH:-${DATA_DIR}/sketch_style.override}"
mkdir -p "$(dirname "${OVERRIDE_PATH}")"

case "${STYLE}" in
  nl|NL|natural) EFFECTIVE="nl" ;;
  jargon|JARGON|expert|sot) EFFECTIVE="jargon" ;;
  *)
    echo "Invalid style: ${STYLE} (use nl or jargon)" >&2
    exit 1
    ;;
esac

printf '%s\n' "${EFFECTIVE}" > "${OVERRIDE_PATH}"
echo "Wrote ${OVERRIDE_PATH} -> ${EFFECTIVE}"

PUBLIC_HOST="${DASHBOARD_PUBLIC_HOST:-}"
if [[ -n "${PUBLIC_HOST}" ]]; then
  CHART_BASE="${CHART_PUBLIC_BASE_URL:-http://${PUBLIC_HOST}/charts}"
  CHART_BASE="${CHART_BASE%/}"
  CURL_ARGS=(-sS -X POST "${CHART_BASE}/admin/sketch-style" -H "Content-Type: application/json" -d "{\"style\":\"${EFFECTIVE}\"}")
  if [[ -n "${CHART_QUERY_API_KEY:-}" ]]; then
    CURL_ARGS+=(-H "X-API-Key: ${CHART_QUERY_API_KEY}")
  fi
  if curl "${CURL_ARGS[@]}"; then
    echo ""
  fi
fi

echo "Effective immediately for sketch + sam-control-plane (shared dbdata mount)."
