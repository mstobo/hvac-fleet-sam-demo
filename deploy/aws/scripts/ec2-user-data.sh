#!/usr/bin/env bash
# Example Amazon Linux 2023 user data: install Docker, clone repo, run Compose.
# Replace placeholders and paste into EC2 "User data" (without the exit 0 guard
# at the top if you want it to run fully unattended).
#
# Required instance role permissions: ECR pull if using private image, optional SSM/Secrets.

set -euo pipefail
# set +e  # uncomment to continue on non-fatal errors during bootstrap

dnf -y update
dnf -y install docker git
systemctl enable docker
systemctl start docker
usermod -aG docker ec2-user || true

INSTALL_ROOT="${INSTALL_ROOT:-/opt/mqtt5sr}"
REPO_URL="${REPO_URL:-}" # e.g. https://github.com/org/mqtt5SRDemo.git
BRANCH="${BRANCH:-main}"

mkdir -p "${INSTALL_ROOT}"
cd "${INSTALL_ROOT}"

if [[ -n "${REPO_URL}" ]]; then
  if [[ ! -d mqtt5SRDemo/.git ]]; then
    git clone --depth 1 --branch "${BRANCH}" "${REPO_URL}" mqtt5SRDemo
  else
    git -C mqtt5SRDemo fetch --depth 1 origin "${BRANCH}" && git -C mqtt5SRDemo checkout "${BRANCH}" && git -C mqtt5SRDemo pull --ff-only || true
  fi
fi

# Place your real env at ${INSTALL_ROOT}/mqtt5SRDemo/deploy/aws/.env (from S3, SSM, or manual copy).
# cp /path/to/secret.env "${INSTALL_ROOT}/mqtt5SRDemo/deploy/aws/.env"

cd mqtt5SRDemo
./deploy/aws/scripts/init-data-dir.sh

# If images are in ECR, log in and set both image URIs (set ACCOUNT/REGION/REPOS in your template).
# aws ecr get-login-password --region "${AWS_REGION}" | docker login --username AWS --password-stdin "${ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com"
# export MQTT5SR_IMAGE="${ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPOSITORY}:latest"
# export MQTT5SR_JAVA_IMAGE="..."   # optional; with --profile java

ENV_FILE=.env docker compose -f deploy/aws/docker-compose.yml up -d
# Optional full stack including Java sample (requires MQTT5SR_JAVA_IMAGE + MQTT_* in .env):
# ENV_FILE=.env docker compose -f deploy/aws/docker-compose.yml --profile java up -d

echo "Bootstrap finished. Check: docker compose -f deploy/aws/docker-compose.yml ps"
