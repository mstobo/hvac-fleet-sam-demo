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

# SAM WebUI starts Uvicorn in a background thread after broker connect (~10–20s).
check_http_retry() {
  local label="$1"
  local url="$2"
  local attempts="${3:-12}"
  local delay="${4:-2}"
  local code="000"
  local i
  for ((i = 1; i <= attempts; i++)); do
    code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 2 --max-time 5 "$url" 2>/dev/null || echo "000")"
    if [[ "$code" =~ ^2|^3 ]]; then
      pass "$label (HTTP $code $url, attempt ${i}/${attempts})"
      return 0
    fi
    [[ "$i" -lt "$attempts" ]] && sleep "$delay"
  done
  fail "$label — HTTP $code $url (not ready after ${attempts} attempts)"
  if [[ "$label" == *"WebUI"* ]]; then
    echo "  WebUI hint: SAM often needs 20–40s after start. Run: sleep 25 && ./healthcheck_demo_stack.sh" >&2
    if grep -q "FastAPI/Uvicorn server starting" /tmp/sam-control-plane.log 2>/dev/null; then
      echo "  Log shows WebUI bind started — keep waiting, then retry curl http://127.0.0.1:${WEBUI_PORT}/" >&2
    else
      echo "  No FastAPI bind line in /tmp/sam-control-plane.log — control plane may have exited; run: tail -40 /tmp/sam-control-plane.log" >&2
    fi
  fi
}

echo "=== Demo stack healthcheck ==="

check_process "Deadband service" "deadband_service.py"
check_process "Sketch service" "sketch_service.py"
check_process "Chart query service" "chart_query_service.py"
if pgrep -fq "run_sam_control_plane.sh" 2>/dev/null || pgrep -fq "sam run configs/agents/main_orchestrator.yaml" 2>/dev/null; then
  pass "SAM control plane (process)"
else
  fail "SAM control plane — not running (run_sam_control_plane.sh / sam orchestrator)"
fi
check_process "Fleet analysis gateway" "sam run configs/gateways/fleet-analysis-gateway.yaml"
check_process "Slack gateway" "sam run configs/gateways/slack-bot.yaml"
check_process "Anomaly service" "anomaly_service.py"
check_process "Analysis→Slack bridge" "analysis_response_to_slack.py"

echo ""
echo "--- HTTP probes ---"
check_http "Chart service /health" "http://127.0.0.1:${CHART_PORT}/health"
check_http_retry "SAM WebUI" "http://127.0.0.1:${WEBUI_PORT}/" 24 2

echo ""
if [[ "$FAIL" -eq 0 ]]; then
  echo "All checks passed."
  exit 0
fi
echo "Some checks failed — see /tmp/sam-*.log"
if grep -q "SOLCLIENT_SUBCODE_MAX_CLIENTS_FOR_QUEUE" /tmp/sam-*.log 2>/dev/null; then
  echo ""
  echo "Hint: Another SAM stack (often AWS Compose) is bound to the same gateway queues on this broker."
  echo "  • Stop the remote stack, or"
  echo "  • Use unique IDs (start_demo_stack.sh sets FLEET_ANALYSIS_GATEWAY_ID / SLACK_GATEWAY_ID / WEBUI_GATEWAY_ID), or"
  echo "  • Use a local-only NAMESPACE in .env (e.g. mqtt_pipeline_optimizer-local/)"
fi
exit 1
