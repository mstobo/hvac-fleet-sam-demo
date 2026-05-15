# AWS deployment (ECR + Docker Compose on EC2)

Production-style flow: **build two images**, **push both to Amazon ECR**, then on EC2 **pull by digest/tag** and run **Compose** with env secrets. SQLite stays on the host via bind mounts.

## Images

| Image | Dockerfile | Contents |
|--------|------------|----------|
| **Python / SAM** | `Dockerfile` | Pipeline services, chart-query, SAM control plane, gateways, anomaly |
| **Java** | `Dockerfile.java` | Maven-built `mqtt5-examples` JAR — default `MQTT5Publisher` (override with `JAVA_MAIN_CLASS`) |

Compose uses **`MQTT5SR_IMAGE`** and **`MQTT5SR_JAVA_IMAGE`** (full ECR URIs including tag).

## What runs in Compose

| Service | Role |
|--------|------|
| `deadband`, `sketch`, `chart-writer`, `demo-publisher` | MQTT pipeline |
| `chart-query` | HTTP API (host port **8010** by default) |
| `sam-control-plane` | Orchestrator + fleet query + Web UI (**8000**) + fleet analysis gateway (MQTT automation) |
| `fleet-analysis-gateway` | **Profile `fleet-analysis-standalone` only** — separate container; can conflict on Solace queue |
| `anomaly` | Anomaly detection |
| **Profile `slack`** | `slack-gateway` + `analysis-to-slack` |
| **Profile `java`** | `java-publisher` (separate ECR image) |

## Morning one-shot (laptop → ECR → optional EC2)

```bash
cp deploy/aws/morning.env.example deploy/aws/morning.env
# Edit morning.env: AWS_REGION, ECR_REPOSITORY, optional EC2_HOST / EC2_KEY / COMPOSE_PROFILES=java

# From your laptop (repo root) — not on EC2; EC2 only receives images via compose after ECR push:
./deploy/aws/scripts/morning-deploy-to-aws.sh
# ./deploy/aws/scripts/morning-deploy-to-aws.sh --no-build --dry-run   # examples
```

This runs `init-data-dir`, builds (unless `--no-build`), pushes to ECR, then either prints manual EC2 steps or SSHes in to `docker compose pull` and `up -d`. EC2 needs `deploy/aws/.env` and an IAM role or AWS CLI for `ecr get-login-password`.

## Prerequisites

- Docker 24+ and Compose v2  
- **Image CPU**: builds default to **`linux/amd64`** (typical EC2 x86). Building on **Apple Silicon** without that would produce **arm64** images and EC2 amd64 will fail (`platform does not match`). Override only for Graviton: `DOCKER_PLATFORM=linux/arm64`.  
- AWS CLI configured for **`aws sts get-caller-identity`**  
- IAM permission to create ECR repos (optional) and push images  
- On EC2: **IAM instance profile** (recommended) or static keys for `aws ecr get-login-password` + `docker login` in the **same account/region** as the registry (see below)  
- **Solace** + **LLM** values in `deploy/aws/.env` (see `env.deploy.example`)  
- For Java in Docker: **`MQTT_BROKER_URL`** in Paho form (e.g. `ssl://…:8883`), **not** `wss://` (Python SAM uses `wss://` in `SOLACE_BROKER_URL` — they can differ)

### EC2: credentials for `docker login` to ECR

If you see **Unable to locate credentials** or **`password is empty`**, the instance has no AWS identity. Prefer an **IAM role attached to the EC2 instance** (no long‑lived keys on disk):

1. In AWS Console: **EC2 → your instance → Actions → Security → Modify IAM role**.  
2. Choose or create a role whose policies allow ECR pulls. The managed policy **`AmazonEC2ContainerRegistryReadOnly`** is enough to pull images in this account.  
3. On the instance, verify: `aws sts get-caller-identity` (should succeed with no `aws configure`).  
4. Retry: `aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin 804666467877.dkr.ecr.us-east-2.amazonaws.com`

Alternative (less ideal): run **`aws configure`** on the instance with an IAM user’s access key that has ECR read permissions (rotate/remove when possible).

## 1. Secrets on disk

```bash
cp deploy/aws/env.deploy.example deploy/aws/.env
# Edit deploy/aws/.env (gitignored)
```

Uncomment and set **`MQTT_BROKER_URL`**, **`MQTT_USERNAME`**, **`MQTT_PASSWORD`** if you use `--profile java`.

## 2. Build images (local or CI)

From the **repository root**:

```bash
./deploy/aws/scripts/init-data-dir.sh
./deploy/aws/scripts/build-image.sh mqtt5sr-demo:latest
./deploy/aws/scripts/build-java-image.sh mqtt5sr-java:latest
```

Or let the push script build first: `BUILD_IMAGES=1` (see below).

## 3. Push both images to ECR

```bash
AWS_REGION=us-east-1 ECR_REPOSITORY=mqtt5sr-demo ./deploy/aws/scripts/push-images-ecr.sh
```

Optional: `ECR_REPOSITORY_JAVA=mqtt5sr-java`, `IMAGE_TAG=v1`, `BUILD_IMAGES=1`.

The script prints `export MQTT5SR_IMAGE=...` and `export MQTT5SR_JAVA_IMAGE=...` for the server.

Single-image push (Python only) remains: `scripts/push-ecr.sh`.

## 4. Run on EC2 (or locally against ECR)

After `docker login` to ECR (or with an instance role that allows pull):

```bash
export MQTT5SR_IMAGE=<uri from push script>
export MQTT5SR_JAVA_IMAGE=<uri from push script>
cd /path/to/mqtt5SRDemo   # compose file + env paths; clone repo on the host

# Once per host: secrets for Compose (broker, LLM, optional Slack / Java MQTT)
cp deploy/aws/env.deploy.example deploy/aws/.env
# Edit deploy/aws/.env (editor of your choice) before any compose up.

./deploy/aws/scripts/init-data-dir.sh   # once — creates SQLite files for bind mounts

ENV_FILE=.env docker compose -f deploy/aws/docker-compose.yml up -d
# Full stack including Java sample:
ENV_FILE=.env docker compose -f deploy/aws/docker-compose.yml --profile java up -d
```

Health: `curl -sf http://127.0.0.1:8010/health`, Web UI `http://<host>:8000`.

### `fleet-analysis-gateway` (restart loop / exit 0)

The gateway needs the **`sam-event-mesh-gateway`** PyPI package (see `sam/requirements.txt`). Images built **before** that dependency was added will crash-loop with `ModuleNotFoundError: sam_event_mesh_gateway` in logs.

**On your laptop (repo root)** — rebuild, verify, push:

```bash
./deploy/aws/scripts/build-image.sh mqtt5sr-demo:latest
# runs verify-python-image.sh unless VERIFY_IMAGE=0
AWS_REGION=us-east-2 ECR_REPOSITORY=hvac/fleet/management ./deploy/aws/scripts/push-images-ecr.sh
# or your existing morning-deploy / push flow
```

**On EC2** — pull and recreate only the gateway (same `.env` + `MQTT5SR_IMAGE` as other SAM services):

```bash
cd /opt/mqtt5sr/mqtt5SRDemo
export MQTT5SR_IMAGE=804666467877.dkr.ecr.us-east-2.amazonaws.com/hvac/fleet/management:latest
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin 804666467877.dkr.ecr.us-east-2.amazonaws.com
docker pull "$MQTT5SR_IMAGE"
ENV_FILE=.env docker compose -f deploy/aws/docker-compose.yml up -d --force-recreate fleet-analysis-gateway
```

**Checks:**

```bash
docker compose -f deploy/aws/docker-compose.yml ps fleet-analysis-gateway
docker compose -f deploy/aws/docker-compose.yml exec -T fleet-analysis-gateway \
  python -c "import sam_event_mesh_gateway; print('plugin ok')"
docker compose -f deploy/aws/docker-compose.yml logs fleet-analysis-gateway --tail=60
```

Healthy logs include broker connect and subscription to `sensors/fleet/analysis-request`. The gateway invokes **FleetQueryAgent** on `sam-control-plane` (keep control plane up and LLM env using `litellm_proxy/...` model names).

If **`RestartCount` keeps increasing** but `import sam_event_mesh_gateway` works, SAM is **exiting during startup** (often broker connect). SAM may exit with code **0**, so Compose still restarts the container:

```bash
docker compose -f deploy/aws/docker-compose.yml logs fleet-analysis-gateway --tail=100
```

Look for `Error initializing flows`, `Error in broker connection`, `ServiceUnreachableError`, or `InitializationError`. Fix Solace vars in `.env` (same as control plane).

**Default AWS layout:** `fleet-analysis-gateway.yaml` is started **inside `sam-control-plane`** (one SAM process). Do **not** run the separate `fleet-analysis-gateway` service unless you use `--profile fleet-analysis-standalone`.

**`Max clients exceeded for queue` / `SOLCLIENT_SUBCODE_MAX_CLIENTS_FOR_QUEUE`:** Not a missing EC2 port. The gateway is binding a **durable queue** that already has a consumer (often a **stale session** from a prior crash loop, a **second container**, or another laptop using the same VPN). Recovery:

```bash
docker compose -f deploy/aws/docker-compose.yml stop fleet-analysis-gateway
sleep 60   # let the broker release the old consumer
ENV_FILE=.env docker compose -f deploy/aws/docker-compose.yml up -d fleet-analysis-gateway
```

In **Solace Cloud**, open the queue used for `sensors/fleet/analysis-request` and disconnect orphaned consumers or raise **Max Clients** if you must use a shared durable queue. Ensure only **one** fleet-analysis-gateway runs against the VPN (stop local `start_demo_stack.sh` if it uses the same broker). The image config sets `temporary_queue: true` on the data-plane client to reduce this on restart.

Foreground debug:

```bash
export MQTT5SR_IMAGE=...
ENV_FILE=.env docker compose -f deploy/aws/docker-compose.yml run --rm --no-deps fleet-analysis-gateway \
  sam run configs/gateways/fleet-analysis-gateway.yaml
```

(Ctrl+C when done; shows the real traceback in your terminal.)

**Slack and a small audience:** The running gateway uses **one** bot + app token pair in `.env`. Invite the app to a **shared channel** (or have people DM the bot). Everyone who Slack allows to message the app can type prompts; SAM still uses your single **LLM** credentials on the server—participants do not need their own API keys.

## Configuration

- **Secrets**: `ENV_FILE=.env` under `deploy/aws/`. Compose injects service wiring (`CHART_QUERY_BASE_URL`, `0.0.0.0` binds, etc.).  
- **Ports**: `CHART_QUERY_PUBLISH_PORT`, `WEBUI_PUBLISH_PORT` (shell or project `.env` for [interpolation](https://docs.docker.com/compose/environment-variables/)).  
- **Data**: SQLite under `deploy/aws/data/` by default; override with `DATA_DIR` (absolute path on EC2).  
- **Java broker env**: `MQTT_BROKER_URL`, `MQTT_USERNAME`, `MQTT_PASSWORD` (see `MqttConfig.java` — env overrides compile-time defaults).

## EC2 bootstrap

See `scripts/ec2-user-data.sh`: install Docker, clone repo, place `deploy/aws/.env`, `init-data-dir`, ECR login, set both image env vars, `docker compose up`.

## Files

| Path | Purpose |
|------|---------|
| `.dockerignore` (repo root) | Shrinks build context; required for `docker build -f deploy/aws/Dockerfile .` |
| `Dockerfile` | Python 3.11 + SAM + `paho-mqtt` |
| `Dockerfile.java` | Java 11 JRE + Maven-built publisher/subscriber |
| `docker-compose.yml` | Stack + optional `java` / `slack` profiles |
| `env.deploy.example` | Env template |
| `scripts/init-data-dir.sh` | SQLite bind-mount files |
| `scripts/build-image.sh` | Build Python image |
| `scripts/build-java-image.sh` | Build Java image |
| `scripts/push-ecr.sh` | Push one image (legacy / Python-only) |
| `scripts/push-images-ecr.sh` | **Push Python + Java to ECR** |
| `scripts/morning-deploy-to-aws.sh` | **Build + push + optional EC2 refresh** (uses `morning.env`) |
| `morning.env.example` | Copy to `morning.env` (gitignored) for region / ECR / EC2 SSH |
| `scripts/ec2-user-data.sh` | Example first-boot |
