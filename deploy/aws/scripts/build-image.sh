#!/usr/bin/env bash
# Build the single Python+SAM image from the repository root.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TAG="${1:-mqtt5sr-demo:latest}"
docker build -f "${ROOT}/deploy/aws/Dockerfile" -t "${TAG}" "${ROOT}"
echo "Built ${TAG}"
