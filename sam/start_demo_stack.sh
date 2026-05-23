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
  pkill -f "deadband_service.py" 2>/dev/null || true
  pkill -f "sketch_service.py" 2>/dev/null || true
  pkill -f "chart_writer_service.py" 2>/dev/null || true
  sleep 2
fi

set -a
[[ -f .env ]] && . ./.env
set +a

export FLEET_QUERY_DEBUG_SKETCH_EVIDENCE="${FLEET_QUERY_DEBUG_SKETCH_EVIDENCE:-true}"
export CHART_QUERY_BASE_URL="${CHART_QUERY_BASE_URL:-http://127.0.0.1:8010}"
# Optional: plot links in Slack use CHART_PUBLIC_BASE_URL when set (e.g. LAN IP:8010); else same as above.

# Avoid Solace exclusive-queue clashes with another SAM stack on the same broker (e.g. AWS demo).
export USE_TEMPORARY_QUEUES="${USE_TEMPORARY_QUEUES:-true}"
export FLEET_ANALYSIS_GATEWAY_ID="${FLEET_ANALYSIS_GATEWAY_ID:-fleet-analysis-gw-local}"
export SLACK_GATEWAY_ID="${SLACK_GATEWAY_ID:-slack-gw-local}"
export WEBUI_GATEWAY_ID="${WEBUI_GATEWAY_ID:-webui-gw-local}"

# shellcheck source=resolve_venv.sh
source "${ROOT}/resolve_venv.sh"
if ! resolve_demo_venv "$ROOT"; then
  echo "Missing or incomplete venv (need Python 3.10–3.13 + solace-agent-mesh)." >&2
  echo "  Do NOT use macOS python3 if it is 3.14." >&2
  echo "  Run once:  ./setup_venv.sh" >&2
  echo "  Then:      source .venv/bin/activate" >&2
  exit 1
fi
VENV_PY="${DEMO_VENV_PY}"
SAM_DEMO="${ROOT}/sam_demo_cli.sh"
if [[ ! -x "$SAM_DEMO" ]]; then
  echo "Missing ${SAM_DEMO}" >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  echo "Missing .env — copy broker/LLM settings:" >&2
  echo "  cp .env.example .env" >&2
  echo "  # or: cp ../mqtt5SRDemo/sam/.env .env" >&2
  exit 1
fi

echo "[1/7] MQTT pipeline (deadband → sketch → chart writer; dashboard columns 2–3)..."
./start_pipeline_services.sh
sleep 1

echo "[2/7] Chart query HTTP (port ${CHART_QUERY_PORT:-8010})..."
nohup "$VENV_PY" -u src/chart_query_service.py >> /tmp/sam-chart-query.log 2>&1 &
sleep 1

echo "[3/7] SAM control plane (orchestrator + fleet_query + WebUI :8000)..."
nohup ./run_sam_control_plane.sh >> /tmp/sam-control-plane.log 2>&1 &
sleep 4

echo "[4/7] Fleet analysis gateway (MQTT → FleetQueryAgent)..."
nohup "$SAM_DEMO" run configs/gateways/fleet-analysis-gateway.yaml >> /tmp/sam-fleet-analysis-gateway.log 2>&1 &
sleep 2

echo "[5/7] Slack gateway (Socket Mode, inbound/outbound Slack)..."
nohup "$SAM_DEMO" run configs/gateways/slack-bot.yaml >> /tmp/sam-slack-gateway.log 2>&1 &
sleep 2

echo "[6/7] Anomaly service (alerts + fleet Slack + auto-analysis trigger)..."
nohup "$VENV_PY" -u src/anomaly_service.py >> /tmp/anomaly-service.log 2>&1 &
sleep 1

echo "[7/7] Analysis → Slack bridge..."
export SLACK_ALERT_CHANNEL
nohup "$VENV_PY" -u src/analysis_response_to_slack.py >> /tmp/analysis-response-to-slack.log 2>&1 &

echo ""
echo ""
echo "Startup commands dispatched. Tail logs under /tmp/sam-*.log"
echo "Optional simulated sensors (instead of dashboard sliders): ./start_traffic_generation.sh"
echo "Run:  sleep 15 && ./healthcheck_demo_stack.sh   # WebUI can take ~10–20s to listen on :8000"
