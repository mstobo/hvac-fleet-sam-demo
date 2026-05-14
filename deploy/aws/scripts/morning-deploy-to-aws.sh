#!/usr/bin/env bash
# One-shot “first thing in the morning”: build images → push to ECR → optionally SSH to EC2 and restart Compose.
#
# Run from your developer machine at the repository root (Docker + AWS CLI for ECR).
# Do not run this script on EC2 unless you deliberately copy deploy/aws/morning.env there and
# intend to build/push from the instance; on EC2 you normally only run docker compose pull/up.
#
# Prep (once):
#   cp deploy/aws/morning.env.example deploy/aws/morning.env
#   Fill AWS_REGION, ECR_REPOSITORY, and (optional) EC2_* for remote refresh.
# On EC2 beforehand: clone repo, create deploy/aws/.env from env.deploy.example, instance role can pull ECR.
#
# Run (from repo root):
#   ./deploy/aws/scripts/morning-deploy-to-aws.sh
#   ./deploy/aws/scripts/morning-deploy-to-aws.sh --no-build     # reuse local images
#   ./deploy/aws/scripts/morning-deploy-to-aws.sh --python-only  # no Java image
#   ./deploy/aws/scripts/morning-deploy-to-aws.sh --dry-run      # skip SSH even if EC2_HOST is set
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
MORNING_ENV="${ROOT}/deploy/aws/morning.env"

DO_BUILD=1
WITH_JAVA=1
DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-build) DO_BUILD=0 ;;
    --python-only) WITH_JAVA=0 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      sed -n '1,22p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
  shift
done

if [[ -f "${MORNING_ENV}" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${MORNING_ENV}"
  set +a
  echo "[morning] Loaded ${MORNING_ENV}"
else
  echo "[morning] No ${MORNING_ENV} — set AWS_REGION and ECR_REPOSITORY in the environment or create morning.env"
  echo "[morning] NOTE: EC2_* must live in that file (repo root is NOT scanned for morning.env)."
fi

: "${AWS_REGION:?Set AWS_REGION (e.g. in deploy/aws/morning.env)}"
: "${ECR_REPOSITORY:?Set ECR_REPOSITORY (e.g. mqtt5sr-demo)}"

ECR_REPOSITORY_JAVA="${ECR_REPOSITORY_JAVA:-mqtt5sr-java}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
EC2_REPO_PATH="${EC2_REPO_PATH:-/opt/mqtt5sr/mqtt5SRDemo}"
EC2_USER="${EC2_USER:-ec2-user}"

command -v docker >/dev/null || {
  echo "docker not found in PATH" >&2
  exit 1
}
docker info >/dev/null 2>&1 || {
  echo "Docker daemon not reachable. Start Docker Desktop (or the engine) and retry." >&2
  exit 1
}
command -v aws >/dev/null || {
  echo "aws CLI not found in PATH" >&2
  exit 1
}

echo "[morning] AWS identity:"
aws sts get-caller-identity --output text

AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
MQTT5SR_IMAGE="${REGISTRY}/${ECR_REPOSITORY}:${IMAGE_TAG}"
MQTT5SR_JAVA_IMAGE="${REGISTRY}/${ECR_REPOSITORY_JAVA}:${IMAGE_TAG}"

echo "[morning] SQLite data dir (local / optional):"
"${ROOT}/deploy/aws/scripts/init-data-dir.sh"

if [[ ! -f "${ROOT}/deploy/aws/.env" ]]; then
  echo "[morning] NOTE: deploy/aws/.env not found on this machine — OK for ECR push from laptop."
  echo "         EC2 must still have deploy/aws/.env for compose (copy from env.deploy.example)."
fi

if [[ "${DO_BUILD}" == "1" ]]; then
  export BUILD_IMAGES=1
else
  export BUILD_IMAGES=0
fi

if [[ "${WITH_JAVA}" == "1" ]]; then
  echo "[morning] Building (if requested) and pushing Python + Java images..."
  IMAGE_TAG="${IMAGE_TAG}" \
    AWS_REGION="${AWS_REGION}" \
    ECR_REPOSITORY="${ECR_REPOSITORY}" \
    ECR_REPOSITORY_JAVA="${ECR_REPOSITORY_JAVA}" \
    BUILD_IMAGES="${BUILD_IMAGES}" \
    LOCAL_IMAGE_PYTHON="mqtt5sr-demo:${IMAGE_TAG}" \
    LOCAL_IMAGE_JAVA="mqtt5sr-java:${IMAGE_TAG}" \
    "${ROOT}/deploy/aws/scripts/push-images-ecr.sh"
else
  echo "[morning] Python/SAM image only (no Java)..."
  if [[ "${BUILD_IMAGES}" == "1" ]]; then
    "${ROOT}/deploy/aws/scripts/build-image.sh" "mqtt5sr-demo:${IMAGE_TAG}"
  fi
  IMAGE_TAG="${IMAGE_TAG}" \
    AWS_REGION="${AWS_REGION}" \
    ECR_REPOSITORY="${ECR_REPOSITORY}" \
    LOCAL_IMAGE="mqtt5sr-demo:${IMAGE_TAG}" \
    "${ROOT}/deploy/aws/scripts/push-ecr.sh"
fi

echo ""
echo "=== Image URIs (export on EC2 or use below) ==="
echo "export MQTT5SR_IMAGE=${MQTT5SR_IMAGE}"
if [[ "${WITH_JAVA}" == "1" ]]; then
  echo "export MQTT5SR_JAVA_IMAGE=${MQTT5SR_JAVA_IMAGE}"
fi
echo "export AWS_REGION=${AWS_REGION}"
echo ""

if [[ -n "${EC2_HOST:-}" ]]; then
  echo "[morning] EC2_HOST is set (${EC2_HOST}) — will attempt SSH after this block (needs EC2_KEY)."
else
  echo "[morning] EC2_HOST is not set — skipping SSH (remote compose pull/up will not run from this script)."
  echo "[morning] Fix: put *uncommented* EC2_HOST=... and EC2_KEY=... in ${MORNING_ENV} (path: deploy/aws/morning.env from repo root)."
fi
echo ""

if [[ -z "${EC2_HOST:-}" ]]; then
  echo "=== EC2 (manual) ==="
  echo "On the instance (repo root = ${EC2_REPO_PATH}):"
  echo "  cd ${EC2_REPO_PATH}"
  echo "  git pull   # if you track compose changes"
  echo "  # Once per host: copy env template and edit secrets (Solace, LLM, optional Slack / Java MQTT) before compose:"
  echo "  #   cp deploy/aws/env.deploy.example deploy/aws/.env && nano deploy/aws/.env"
  echo "  # Once: ./deploy/aws/scripts/init-data-dir.sh"
  echo "  aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${REGISTRY}"
  echo "  export MQTT5SR_IMAGE=${MQTT5SR_IMAGE}"
  if [[ "${WITH_JAVA}" == "1" ]]; then
    echo "  export MQTT5SR_JAVA_IMAGE=${MQTT5SR_JAVA_IMAGE}"
  fi
  echo "  ENV_FILE=.env docker compose -f deploy/aws/docker-compose.yml pull"
  if [[ -n "${COMPOSE_PROFILES:-}" ]]; then
    echo "  COMPOSE_PROFILES=${COMPOSE_PROFILES} ENV_FILE=.env docker compose -f deploy/aws/docker-compose.yml up -d"
  else
    echo "  ENV_FILE=.env docker compose -f deploy/aws/docker-compose.yml up -d"
    echo "  # Optional: --profile java and/or --profile slack instead of COMPOSE_PROFILES"
  fi
  exit 0
fi

if [[ "${DRY_RUN}" == "1" ]]; then
  echo "[morning] --dry-run: skipping SSH to ${EC2_HOST}"
  exit 0
fi

: "${EC2_KEY:?Set EC2_KEY in morning.env to the SSH private key path for ${EC2_HOST}}"

if [[ ! -f "${EC2_KEY}" ]]; then
  echo "EC2_KEY file not found: ${EC2_KEY}" >&2
  exit 1
fi

SSH=(ssh -i "${EC2_KEY}" -o BatchMode=yes -o StrictHostKeyChecking=accept-new "${EC2_USER}@${EC2_HOST}")

echo "[morning] Remote refresh on ${EC2_USER}@${EC2_HOST} (${EC2_REPO_PATH})..."

"${SSH[@]}" bash -s <<EOF
set -euo pipefail
export AWS_DEFAULT_REGION="${AWS_REGION}"
export MQTT5SR_IMAGE="${MQTT5SR_IMAGE}"
$([[ "${WITH_JAVA}" == "1" ]] && echo "export MQTT5SR_JAVA_IMAGE=\"${MQTT5SR_JAVA_IMAGE}\"")
$([[ -n "${COMPOSE_PROFILES:-}" ]] && echo "export COMPOSE_PROFILES=\"${COMPOSE_PROFILES}\"")
if [[ ! -d "${EC2_REPO_PATH}" ]]; then
  echo "[morning/remote] ERROR: directory does not exist: ${EC2_REPO_PATH}" >&2
  echo "[morning/remote] Clone the repo there (see deploy/aws/scripts/ec2-user-data.sh) or set EC2_REPO_PATH in deploy/aws/morning.env on your laptop." >&2
  exit 1
fi
cd "${EC2_REPO_PATH}"
aws ecr get-login-password --region "${AWS_REGION}" | docker login --username AWS --password-stdin "${REGISTRY}"
ENV_FILE=.env docker compose -f deploy/aws/docker-compose.yml pull
ENV_FILE=.env docker compose -f deploy/aws/docker-compose.yml up -d
EOF

echo "[morning] Done. Check: ssh -i <key> ${EC2_USER}@${EC2_HOST} 'cd ${EC2_REPO_PATH} && docker compose -f deploy/aws/docker-compose.yml ps'"
