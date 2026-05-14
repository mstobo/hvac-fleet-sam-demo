#!/usr/bin/env bash
# Build the single Python+SAM image from the repository root.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TAG="${1:-mqtt5sr-demo:latest}"
REQ="${ROOT}/sam/requirements.txt"
if [[ ! -f "${REQ}" ]]; then
  echo "ERROR: ${REQ} not found. Context must be the repository root (directory that contains sam/)." >&2
  exit 1
fi
if [[ ! -f "${ROOT}/.dockerignore" ]]; then
  echo "ERROR: ${ROOT}/.dockerignore missing. Docker only reads .dockerignore from the build context root (repo root)." >&2
  exit 1
fi
docker build -f "${ROOT}/deploy/aws/Dockerfile" -t "${TAG}" "${ROOT}"
echo "Built ${TAG}"
