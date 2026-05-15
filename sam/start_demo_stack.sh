#!/usr/bin/env bash
# Bring up the full HVAC fleet demo stack (SAM agents + gateways + pipeline helpers).
# Usage:
#   ./start_demo_stack.sh           # start (skip if already running — use healthcheck)
#   ./start_demo_stack.sh --fresh   # stop matching demo processes, then start clean
#
# Prerequisites: .env in this directory (broker, Slack tokens, SLACK_ALERT_CHANNEL, etc.)
# Simulated sensor traffic + pipeline stages: ./start_traffic_generation.sh
# Logs: /tmp/sam-*.log and /tmp/anomaly-service.log, /tmp/analysis-response-to-slack.log

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ "${1:-}" == "--fresh" ]]; then
  echo "[start_demo_stack] --fresh: stopping existing demo processes..."
  pkill -f "run_sam_control_plane.sh" 2>/dev/null || true
  pkill -f "sam run configs/agents/main_orchestrator.yaml" 2>/dev/null || true
  pkill -f "sam run configs/gateways/slack-bot.yaml" 2>/dev/null || true
  pkill -f "sam run configs/gateways/fleet-analysis-gateway.yaml" 2>/dev/null || true
  pkill -f "analysis_response_to_slack.py" 2>/dev/null || true
  pkill -f "src/anomaly_service.py" 2>/dev/null || true
  pkill -f "chart_query_service.py" 2>/dev/null || true
  sleep 2
fi

set -a
[[ -f .env ]] && . ./.env
set +a

export FLEET_QUERY_DEBUG_SKETCH_EVIDENCE="${FLEET_QUERY_DEBUG_SKETCH_EVIDENCE:-true}"
export CHART_QUERY_BASE_URL="${CHART_QUERY_BASE_URL:-http://127.0.0.1:8010}"
# Optional: plot links in Slack use CHART_PUBLIC_BASE_URL when set (e.g. LAN IP:8010); else same as above.

VENV_PY="./.venv/bin/python"
VENV_SAM="./.venv/bin/sam"

if [[ ! -x "$VENV_PY" ]] || [[ ! -x "$VENV_SAM" ]]; then
  echo "Missing .venv; create it and install deps first." >&2
  exit 1
fi

echo "[1/6] Chart query HTTP (port ${CHART_QUERY_PORT:-8010})..."
nohup "$VENV_PY" -u src/chart_query_service.py >> /tmp/sam-chart-query.log 2>&1 &
sleep 1

echo "[2/6] SAM control plane (orchestrator + fleet_query + WebUI :8000)..."
nohup ./run_sam_control_plane.sh >> /tmp/sam-control-plane.log 2>&1 &
sleep 4

echo "[3/6] Fleet analysis gateway (MQTT → FleetQueryAgent)..."
nohup "$VENV_SAM" run configs/gateways/fleet-analysis-gateway.yaml >> /tmp/sam-fleet-analysis-gateway.log 2>&1 &
sleep 2

echo "[4/6] Slack gateway (Socket Mode, inbound/outbound Slack)..."
nohup "$VENV_SAM" run configs/gateways/slack-bot.yaml >> /tmp/sam-slack-gateway.log 2>&1 &
sleep 2

echo "[5/6] Anomaly service (alerts + fleet Slack + auto-analysis trigger)..."
nohup "$VENV_PY" -u src/anomaly_service.py >> /tmp/anomaly-service.log 2>&1 &
sleep 1

echo "[6/6] Analysis → Slack bridge..."
export SLACK_ALERT_CHANNEL
nohup "$VENV_PY" -u src/analysis_response_to_slack.py >> /tmp/analysis-response-to-slack.log 2>&1 &

echo ""
echo "Startup commands dispatched. Tail logs under /tmp/sam-*.log"
echo "Run:  ./healthcheck_demo_stack.sh"
