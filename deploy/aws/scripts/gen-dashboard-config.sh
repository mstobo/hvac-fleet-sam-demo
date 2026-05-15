#!/usr/bin/env bash
# Build demo_dashboard.config.json for the static pipeline dashboard (browser MQTT creds).
# Reads Solace settings from deploy/aws/.env — do not commit the output if it contains secrets.
#
# Usage (on EC2 after editing .env):
#   export DASHBOARD_PUBLIC_HOST=18.x.x.x
#   ./deploy/aws/scripts/gen-dashboard-config.sh deploy/aws/.env
#   sudo cp sam/demo_dashboard.config.json /var/www/mqtt5sr-demo/
#   sudo chown www-data:www-data /var/www/mqtt5sr-demo/demo_dashboard.config.json
#
# Or write directly under /var/www (requires sudo):
#   sudo ./deploy/aws/scripts/gen-dashboard-config.sh deploy/aws/.env /var/www/mqtt5sr-demo/demo_dashboard.config.json
#
set -euo pipefail

ENV_FILE="${1:-deploy/aws/.env}"
DEPLOY_TARGET="${2:-${DASHBOARD_WEB_ROOT:-/var/www/mqtt5sr-demo}/demo_dashboard.config.json}"
OUT="sam/demo_dashboard.config.json"

if [[ -n "${2:-}" && ( -w "$(dirname "${2}")" || -w "${2}" ) ]]; then
  OUT="${2}"
  DEPLOY_TARGET=""
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing env file: ${ENV_FILE}" >&2
  exit 1
fi

# shellcheck disable=SC1090
set -a
source "${ENV_FILE}"
set +a

WS_HOST="${DASHBOARD_WS_HOST:-}"
# MQTT WebSocket on Solace Cloud is typically 8443 (SAM wss in SOLACE_BROKER_URL is often :443).
WS_PORT="${DASHBOARD_WS_PORT:-8443}"

if [[ -z "${WS_HOST}" && -n "${SOLACE_BROKER_URL:-}" ]]; then
  stripped="${SOLACE_BROKER_URL#*://}"
  WS_HOST="${stripped%%/*}"
  WS_HOST="${WS_HOST%%:*}"
fi

PUBLIC_HOST="${DASHBOARD_PUBLIC_HOST:-}"
CHART_BASE=""
SAM_BASE=""
if [[ -n "${PUBLIC_HOST}" ]]; then
  CHART_BASE="http://${PUBLIC_HOST}/charts"
  SAM_BASE="http://${PUBLIC_HOST}:8000"
fi

export WS_HOST WS_PORT CHART_BASE SAM_BASE
mkdir -p "$(dirname "${OUT}")"
python3 - "${OUT}" << PY
import json, os, sys
out = sys.argv[1]
site = os.environ.get("DC_BROKER_SITE", "Hub") or "Hub"
cfg = {
    "wsHost": os.environ.get("WS_HOST") or "",
    "wsPort": int(os.environ.get("WS_PORT") or "8443"),
    "wsUser": os.environ.get("SOLACE_BROKER_USERNAME") or "",
    "wsPass": os.environ.get("SOLACE_BROKER_PASSWORD") or "",
    "rawTopicBase": f"dc/{site}/v1/raw/dc1/hall-a/row-a3/rack-12",
    "chartQueryBaseUrl": os.environ.get("CHART_BASE") or "",
    "samWebuiBaseUrl": os.environ.get("SAM_BASE") or "",
    "autoConnect": True,
}
with open(out, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print(f"Wrote {out} (wsHost={cfg['wsHost']!r}, autoConnect=true)")
PY

if [[ -n "${DEPLOY_TARGET}" && "${OUT}" != "${DEPLOY_TARGET}" ]]; then
  echo ""
  echo "Publish beside index.html:"
  echo "  sudo cp ${OUT} ${DEPLOY_TARGET}"
  echo "  sudo chown www-data:www-data ${DEPLOY_TARGET}"
fi
