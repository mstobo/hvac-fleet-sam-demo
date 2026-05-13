#!/usr/bin/env bash
# Orchestrator + FleetQueryAgent + HTTP WebUI (FastAPI on FASTAPI_PORT, default 8000).
# Chart/content DB agents run as separate microservices if you use them.
set -euo pipefail
cd "$(dirname "$0")"
set -a
[ -f .env ] && . ./.env
set +a
export FLEET_QUERY_DEBUG_SKETCH_EVIDENCE="${FLEET_QUERY_DEBUG_SKETCH_EVIDENCE:-true}"
export CHART_QUERY_BASE_URL="${CHART_QUERY_BASE_URL:-http://127.0.0.1:8010}"
exec ./.venv/bin/sam run \
  configs/agents/main_orchestrator.yaml \
  configs/agents/fleet_query_agent.yaml \
  configs/gateways/webui.yaml
