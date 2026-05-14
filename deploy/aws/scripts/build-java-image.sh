#!/usr/bin/env bash
# Build the Java MQTT sample image from the repository root.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TAG="${1:-mqtt5sr-java:latest}"
docker build -f "${ROOT}/deploy/aws/Dockerfile.java" -t "${TAG}" "${ROOT}"
echo "Built ${TAG}"
