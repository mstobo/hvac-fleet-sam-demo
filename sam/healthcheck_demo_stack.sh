#!/usr/bin/env bash
# Verify demo stack processes and key HTTP endpoints.
# Exit 0 if all checks pass; non-zero if any required check fails.
#
# Optional: CHART_QUERY_PORT, WEBUI_PORT (defaults 8010, 8000)

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

set -a
[[ -f .env ]] && . ./.env
set +a

CHART_PORT="${CHART_QUERY_PORT:-8010}"
WEBUI_PORT="${FASTAPI_PORT:-${WEBUI_PORT:-8000}}"

FAIL=0
pass() { echo "  OK  $*"; }
fail() { echo "  FAIL $*" >&2; FAIL=1; }

check_process() {
  local label="$1"
  local pattern="$2"
  if pgrep -fq "$pattern" 2>/dev/null; then
    pass "$label (process)"
  else
    fail "$label — no process matching: $pattern"
  fi
}

check_http() {
  local label="$1"
  local url="$2"
  local code
  code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 2 --max-time 5 "$url" || echo "000")"
  if [[ "$code" =~ ^2|^3 ]]; then
    pass "$label (HTTP $code $url)"
  else
    fail "$label — HTTP $code $url"
  fi
}

echo "=== Demo stack healthcheck ==="

check_process "Chart query service" "chart_query_service.py"
check_process "SAM control plane" "sam run configs/agents/main_orchestrator.yaml"
check_process "Fleet analysis gateway" "sam run configs/gateways/fleet-analysis-gateway.yaml"
check_process "Slack gateway" "sam run configs/gateways/slack-bot.yaml"
check_process "Anomaly service" "anomaly_service.py"
check_process "Analysis→Slack bridge" "analysis_response_to_slack.py"

echo ""
echo "--- HTTP probes ---"
check_http "Chart service /health" "http://127.0.0.1:${CHART_PORT}/health"
check_http "SAM WebUI" "http://127.0.0.1:${WEBUI_PORT}/"

echo ""
if [[ "$FAIL" -eq 0 ]]; then
  echo "All checks passed."
  exit 0
fi
echo "Some checks failed — see /tmp/sam-*.log"
exit 1
