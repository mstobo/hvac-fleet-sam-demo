#!/usr/bin/env bash
# Verify the Python/SAM image includes gateway plugins and Slack SDK.
# Usage: ./deploy/aws/scripts/verify-python-image.sh [image-ref]
#   image-ref defaults to mqtt5sr-demo:latest
set -euo pipefail
IMAGE="${1:-mqtt5sr-demo:latest}"
echo "[verify-python-image] Image: ${IMAGE}"
docker run --rm --entrypoint python "${IMAGE}" -c "
import sam_event_mesh_gateway
import sam_slack_gateway_adapter
import slack_sdk
from importlib.metadata import version, PackageNotFoundError

print('sam_event_mesh_gateway:', sam_event_mesh_gateway.__file__)
print('sam_slack_gateway_adapter:', sam_slack_gateway_adapter.__file__)
try:
    slack_ver = version('slack_sdk')
except PackageNotFoundError:
    slack_ver = getattr(slack_sdk, '__version__', 'unknown')
print('slack_sdk:', slack_ver)
"
