# Build from repository root:
#   docker build -f deploy/aws/Dockerfile -t mqtt5sr-demo:latest .
#
# Runtime WORKDIR is /app/sam (matches local dev layout). Override command per service in Compose / ECS.

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/sam

COPY sam/requirements.txt ./requirements.txt
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && pip install "paho-mqtt>=2.1.0,<3"

COPY sam/ ./

# Default: idle (Compose / ECS set command per service)
CMD ["sleep", "infinity"]
