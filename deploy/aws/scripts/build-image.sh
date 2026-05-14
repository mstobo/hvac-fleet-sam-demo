#!/usr/bin/env bash
# Build the single Python+SAM image from the repository root.
#
# Default target is linux/amd64 (typical EC2 x86). On Apple Silicon, Docker cross-builds.
# Override for Graviton-only deploys: DOCKER_PLATFORM=linux/arm64 ./deploy/aws/scripts/build-image.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TAG="${1:-mqtt5sr-demo:latest}"
DOCKER_PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"
REQ="${ROOT}/sam/requirements.txt"
if [[ ! -f "${REQ}" ]]; then
  echo "ERROR: ${REQ} not found. Context must be the repository root (directory that contains sam/)." >&2
  exit 1
fi
if [[ ! -f "${ROOT}/.dockerignore" ]]; then
  echo "ERROR: ${ROOT}/.dockerignore missing. Docker only reads .dockerignore from the build context root (repo root)." >&2
  exit 1
fi
echo "[build-image] Platform: ${DOCKER_PLATFORM} (set DOCKER_PLATFORM to change, e.g. linux/arm64 for Graviton EC2)"
docker build --platform "${DOCKER_PLATFORM}" -f "${ROOT}/deploy/aws/Dockerfile" -t "${TAG}" "${ROOT}"
echo "Built ${TAG}"
