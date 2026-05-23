#!/usr/bin/env bash
# Start simulated sensor traffic and the deterministic MQTT pipeline stages that feed it.
#
# Starts (microservice layout):
#   1. deadband_service   — raw → filtered / suppressed
#   2. sketch_service     — filtered → sketched (for anomaly + SAM tools)
#   3. chart_writer_service — filtered/suppressed → chart_data.db rollups
#   4. demo_publisher     — synthetic raw readings into dc/<DC_BROKER_SITE>/v1/raw/...
#
# Run AFTER broker credentials are in .env. For full demo also run:
#   ./start_demo_stack.sh   (SAM + anomaly on dc/<DC_BROKER_SITE>/v1/pipeline/sketched)
#
# Usage:
#   ./start_traffic_generation.sh
#   ./start_traffic_generation.sh --fresh   # stop matching pipeline processes, then start
#
# Logs: /tmp/sam-deadband.log, /tmp/sam-sketch.log, /tmp/sam-chart-writer.log, /tmp/sam-demo-publisher.log

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ "${1:-}" == "--fresh" ]]; then
  echo "[start_traffic_generation] --fresh: stopping existing pipeline processes..."
  pkill -f "deadband_service.py" 2>/dev/null || true
  pkill -f "sketch_service.py" 2>/dev/null || true
  pkill -f "chart_writer_service.py" 2>/dev/null || true
  pkill -f "demo_publisher.py" 2>/dev/null || true
  sleep 2
fi

set -a
[[ -f .env ]] && . ./.env
set +a

# shellcheck source=resolve_venv.sh
source "${ROOT}/resolve_venv.sh"
if ! resolve_demo_venv "$ROOT"; then
  echo "Missing venv. Run: ./setup_venv.sh" >&2
  exit 1
fi
VENV_PY="${DEMO_VENV_PY}"

echo "[1/2] Pipeline microservices (deadband → sketch → chart writer)..."
./start_pipeline_services.sh
sleep 1

echo "[2/2] Demo publisher (simulated sensors)..."
nohup "$VENV_PY" -u src/demo_publisher.py >> /tmp/sam-demo-publisher.log 2>&1 &

echo ""
echo "Traffic pipeline started. Logs under /tmp/sam-{deadband,sketch,chart-writer,demo-publisher}.log"
echo "Ensure ./start_demo_stack.sh is running so anomaly_service consumes dc/<DC_BROKER_SITE>/v1/pipeline/sketched (default Hub)."
