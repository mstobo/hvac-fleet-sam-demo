#!/usr/bin/env bash
# Orchestrator + FleetQueryAgent + HTTP WebUI (FastAPI on FASTAPI_PORT, default 8000).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
set -a
[[ -f .env ]] && . ./.env
set +a

# shellcheck source=resolve_venv.sh
source "${ROOT}/resolve_venv.sh"
if ! resolve_demo_venv "$ROOT"; then
  echo "SAM not installed. Run: ./setup_venv.sh && source .venv/bin/activate" >&2
  exit 1
fi

export FLEET_QUERY_DEBUG_SKETCH_EVIDENCE="${FLEET_QUERY_DEBUG_SKETCH_EVIDENCE:-true}"
export CHART_QUERY_BASE_URL="${CHART_QUERY_BASE_URL:-http://127.0.0.1:8010}"
export USE_TEMPORARY_QUEUES="${USE_TEMPORARY_QUEUES:-true}"

sam_cli_exec run \
  configs/agents/main_orchestrator.yaml \
  configs/agents/fleet_query_agent.yaml \
  configs/gateways/webui.yaml
