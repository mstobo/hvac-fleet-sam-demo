#!/usr/bin/env bash
# Build the Java MQTT sample image from the repository root.
#
# Default linux/amd64 for typical EC2 x86. Override: DOCKER_PLATFORM=linux/arm64
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TAG="${1:-mqtt5sr-java:latest}"
DOCKER_PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"
if [[ ! -f "${ROOT}/pom.xml" ]]; then
  echo "ERROR: ${ROOT}/pom.xml not found. Java image build context must be the repository root." >&2
  exit 1
fi
if [[ ! -f "${ROOT}/.dockerignore" ]]; then
  echo "ERROR: ${ROOT}/.dockerignore missing (repo root)." >&2
  exit 1
fi
echo "[build-java-image] Platform: ${DOCKER_PLATFORM}"
docker build --platform "${DOCKER_PLATFORM}" -f "${ROOT}/deploy/aws/Dockerfile.java" -t "${TAG}" "${ROOT}"
echo "Built ${TAG}"
