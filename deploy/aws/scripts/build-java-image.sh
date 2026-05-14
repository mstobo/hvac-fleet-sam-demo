#!/usr/bin/env bash
# Build the Java MQTT sample image from the repository root.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TAG="${1:-mqtt5sr-java:latest}"
if [[ ! -f "${ROOT}/pom.xml" ]]; then
  echo "ERROR: ${ROOT}/pom.xml not found. Java image build context must be the repository root." >&2
  exit 1
fi
if [[ ! -f "${ROOT}/.dockerignore" ]]; then
  echo "ERROR: ${ROOT}/.dockerignore missing (repo root)." >&2
  exit 1
fi
docker build -f "${ROOT}/deploy/aws/Dockerfile.java" -t "${TAG}" "${ROOT}"
echo "Built ${TAG}"
