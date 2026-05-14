#!/usr/bin/env bash
# Create host SQLite files for bind mounts used by deploy/aws/docker-compose.yml.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DATA="${DATA_DIR:-${ROOT}/deploy/aws/data}"
mkdir -p "${DATA}"
touch "${DATA}/sensor_data.db" "${DATA}/chart_data.db"
chmod 666 "${DATA}/sensor_data.db" "${DATA}/chart_data.db" 2>/dev/null || true
echo "Ready: ${DATA}/sensor_data.db and chart_data.db"
