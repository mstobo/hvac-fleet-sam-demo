#!/usr/bin/env bash
# Build demo_dashboard.config.json for the static pipeline dashboard (browser MQTT creds).
# Reads Solace settings from deploy/aws/.env — do not commit the output if it contains secrets.
#
# Usage (on EC2 after editing .env):
#   ./deploy/aws/scripts/gen-dashboard-config.sh deploy/aws/.env /var/www/mqtt5sr-demo/demo_dashboard.config.json
#   sudo chown www-data:www-data /var/www/mqtt5sr-demo/demo_dashboard.config.json
#
set -euo pipefail

ENV_FILE="${1:-deploy/aws/.env}"
OUT="${2:-sam/demo_dashboard.config.json}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing env file: ${ENV_FILE}" >&2
  exit 1
fi

# shellcheck disable=SC1090
set -a
source "${ENV_FILE}"
set +a

WS_HOST="${DASHBOARD_WS_HOST:-}"
WS_PORT="${DASHBOARD_WS_PORT:-443}"

if [[ -z "${WS_HOST}" && -n "${SOLACE_BROKER_URL:-}" ]]; then
  # wss://mr-connection-xxx.messaging.solace.cloud:443 → hostname + port
  stripped="${SOLACE_BROKER_URL#*://}"
  WS_HOST="${stripped%%/*}"
  WS_HOST="${WS_HOST%%:*}"
  if [[ "${SOLACE_BROKER_URL}" =~ :([0-9]+) ]]; then
    WS_PORT="${BASH_REMATCH[1]}"
  fi
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
    "wsPort": int(os.environ.get("WS_PORT") or "443"),
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

echo "Install next to index.html, e.g.:"
echo "  cp ${OUT} /var/www/mqtt5sr-demo/demo_dashboard.config.json"
