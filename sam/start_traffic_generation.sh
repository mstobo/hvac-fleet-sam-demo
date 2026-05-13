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

VENV_PY="./.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing .venv; create it and install deps first." >&2
  exit 1
fi

echo "[1/4] Deadband filter..."
nohup "$VENV_PY" -u src/deadband_service.py >> /tmp/sam-deadband.log 2>&1 &
sleep 1

echo "[2/4] Sketch generator..."
nohup "$VENV_PY" -u src/sketch_service.py >> /tmp/sam-sketch.log 2>&1 &
sleep 1

echo "[3/4] Chart writer (SQLite rollups)..."
nohup "$VENV_PY" -u src/chart_writer_service.py >> /tmp/sam-chart-writer.log 2>&1 &
sleep 1

echo "[4/4] Demo publisher (simulated sensors)..."
nohup "$VENV_PY" -u src/demo_publisher.py >> /tmp/sam-demo-publisher.log 2>&1 &

echo ""
echo "Traffic pipeline started. Logs under /tmp/sam-{deadband,sketch,chart-writer,demo-publisher}.log"
echo "Ensure ./start_demo_stack.sh is running so anomaly_service consumes dc/<DC_BROKER_SITE>/v1/pipeline/sketched (default Hub)."
