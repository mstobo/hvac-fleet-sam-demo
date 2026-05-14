#!/usr/bin/env bash
# Log in to ECR once, then push the Python/SAM image and the Java MQTT image.
#
# Usage (from anywhere):
#   AWS_REGION=us-east-1 \
#   ECR_REPOSITORY=mqtt5sr-demo \
#   ECR_REPOSITORY_JAVA=mqtt5sr-java \
#   ./deploy/aws/scripts/push-images-ecr.sh
#
# Prerequisites: images already built (see build-image.sh and build-java-image.sh),
# or set BUILD_IMAGES=1 to build both first.
#
# Optional: IMAGE_TAG (default latest), AWS_ACCOUNT_ID, LOCAL_IMAGE_PYTHON, LOCAL_IMAGE_JAVA
set -euo pipefail
: "${AWS_REGION:?Set AWS_REGION (e.g. us-east-1)}"
: "${ECR_REPOSITORY:?Set ECR_REPOSITORY (Python/SAM ECR repo name)}"

ECR_REPOSITORY_JAVA="${ECR_REPOSITORY_JAVA:-mqtt5sr-java}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

LOCAL_IMAGE_PYTHON="${LOCAL_IMAGE_PYTHON:-mqtt5sr-demo:${IMAGE_TAG}}"
LOCAL_IMAGE_JAVA="${LOCAL_IMAGE_JAVA:-mqtt5sr-java:${IMAGE_TAG}}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

if [[ "${BUILD_IMAGES:-0}" == "1" ]]; then
  "${ROOT}/deploy/aws/scripts/build-image.sh" "${LOCAL_IMAGE_PYTHON}"
  "${ROOT}/deploy/aws/scripts/build-java-image.sh" "${LOCAL_IMAGE_JAVA}"
fi

ensure_repo() {
  local name="$1"
  if ! aws ecr describe-repositories --repository-names "${name}" --region "${AWS_REGION}" >/dev/null 2>&1; then
    aws ecr create-repository --repository-name "${name}" --region "${AWS_REGION}" >/dev/null
    echo "Created ECR repository ${name}"
  fi
}

push_one() {
  local local_image="$1"
  local repo="$2"
  local uri="${REGISTRY}/${repo}:${IMAGE_TAG}"
  docker tag "${local_image}" "${uri}"
  docker push "${uri}"
  echo "Pushed ${uri}"
}

ensure_repo "${ECR_REPOSITORY}"
ensure_repo "${ECR_REPOSITORY_JAVA}"

aws ecr get-login-password --region "${AWS_REGION}" | docker login --username AWS --password-stdin "${REGISTRY}"

push_one "${LOCAL_IMAGE_PYTHON}" "${ECR_REPOSITORY}"
push_one "${LOCAL_IMAGE_JAVA}" "${ECR_REPOSITORY_JAVA}"

echo ""
echo "On EC2 (or any host), from the cloned repo root (after deploy/aws/.env exists — see deploy/aws/README.md):"
echo "  cp deploy/aws/env.deploy.example deploy/aws/.env   # once; then edit: Solace, LLM, optional Slack / Java MQTT"
echo "  ./deploy/aws/scripts/init-data-dir.sh              # once (SQLite bind-mount files)"
echo "  export MQTT5SR_IMAGE=${REGISTRY}/${ECR_REPOSITORY}:${IMAGE_TAG}"
echo "  export MQTT5SR_JAVA_IMAGE=${REGISTRY}/${ECR_REPOSITORY_JAVA}:${IMAGE_TAG}"
echo "  ENV_FILE=.env docker compose -f deploy/aws/docker-compose.yml --profile java up -d"
