#!/usr/bin/env bash
# Deterministic MQTT pipeline: deadband → sketch → chart writer.
# Required for dashboard columns 2 (sketch) and 3 (anomaly alerts from rules).
#
# Usage:
#   ./start_pipeline_services.sh
#   ./start_pipeline_services.sh --fresh

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ "${1:-}" == "--fresh" ]]; then
  pkill -f "deadband_service.py" 2>/dev/null || true
  pkill -f "sketch_service.py" 2>/dev/null || true
  pkill -f "chart_writer_service.py" 2>/dev/null || true
  sleep 1
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

echo "[pipeline] Deadband filter (raw → filtered/suppressed)..."
nohup "$VENV_PY" -u src/deadband_service.py >> /tmp/sam-deadband.log 2>&1 &
sleep 1

echo "[pipeline] Sketch generator (filtered → sketched)..."
nohup "$VENV_PY" -u src/sketch_service.py >> /tmp/sam-sketch.log 2>&1 &
sleep 1

echo "[pipeline] Chart writer (SQLite rollups)..."
nohup "$VENV_PY" -u src/chart_writer_service.py >> /tmp/sam-chart-writer.log 2>&1 &

echo "[pipeline] Logs: /tmp/sam-deadband.log, /tmp/sam-sketch.log, /tmp/sam-chart-writer.log"
