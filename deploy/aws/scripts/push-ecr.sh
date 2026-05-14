#!/usr/bin/env bash
# Tag and push a single locally built image to Amazon ECR.
# To push the Python stack **and** the Java image in one step, use push-images-ecr.sh.
# Prerequisites: AWS CLI configured; Docker running.
#
# Usage:
#   ./deploy/aws/scripts/build-image.sh mqtt5sr-demo:latest
#   AWS_REGION=us-east-1 ECR_REPOSITORY=mqtt5sr-demo ./deploy/aws/scripts/push-ecr.sh
#
# Optional: AWS_ACCOUNT_ID (default: sts get-caller-identity), IMAGE_TAG (default: latest),
#           LOCAL_IMAGE (default: mqtt5sr-demo:${IMAGE_TAG})
set -euo pipefail
: "${AWS_REGION:?Set AWS_REGION (e.g. us-east-1)}"
: "${ECR_REPOSITORY:?Set ECR_REPOSITORY (ECR repo name)}"

AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
LOCAL_IMAGE="${LOCAL_IMAGE:-mqtt5sr-demo:${IMAGE_TAG}}"
REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
ECR_URI="${REGISTRY}/${ECR_REPOSITORY}:${IMAGE_TAG}"

if ! aws ecr describe-repositories --repository-names "${ECR_REPOSITORY}" --region "${AWS_REGION}" >/dev/null 2>&1; then
  aws ecr create-repository --repository-name "${ECR_REPOSITORY}" --region "${AWS_REGION}" >/dev/null
  echo "Created ECR repository ${ECR_REPOSITORY}"
fi

aws ecr get-login-password --region "${AWS_REGION}" | docker login --username AWS --password-stdin "${REGISTRY}"
docker tag "${LOCAL_IMAGE}" "${ECR_URI}"
docker push "${ECR_URI}"
echo "Pushed ${ECR_URI}"
echo "On EC2, set MQTT5SR_IMAGE=${ECR_URI} before docker compose up."
