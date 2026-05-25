# HVAC Fleet Monitoring — MQTT Pipeline + Solace Agent Mesh (A2A)

> **Value story (2 pages):** [mstobo.github.io/hvac-fleet-sam-demo](https://mstobo.github.io/hvac-fleet-sam-demo/) — executive framing, outcomes, and cost pattern.  
> **Architecture blog:** [Reason on the Exception](https://mstobo.github.io/hvac-fleet-sam-demo/blog/reason-on-the-exception.html) — data plane vs agents, sketches, measured token runs (source in `docs/blog/`).  
> **Fleet analysis (production):** [docs/FLEET_ANALYSIS_PRODUCTION.md](docs/FLEET_ANALYSIS_PRODUCTION.md) — SECTION A tool budget, env tuning, verification.  
> **Live demo:** [ec2-18-116-251-212.us-east-2.compute.amazonaws.com](http://ec2-18-116-251-212.us-east-2.compute.amazonaws.com/) — pipeline dashboard, digital twin, SAM chat (AWS).  
> **This README** — technical setup, topics, deployment, and troubleshooting.

**Reduce MQTT noise by ~99% and add LLM-powered fleet analysis on demand — without sending every sensor reading to an LLM.**

This repo is the **Agent-Mesh–A2A** branch: deterministic MQTT processing, SQLite/chart storage, SAM agents and gateways, optional Slack, and AWS (ECR + EC2 Compose) deployment.

---

## Overview

High-volume temperature sensors publish to Solace. A **deterministic pipeline** (deadband → sketch → anomaly) filters noise, writes rollups to SQLite, and raises rule-based alerts. **Solace Agent Mesh (SAM)** answers questions and runs **fleet-critical automation** by reading pre-processed data — not raw MQTT.

### Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     DATA PLANE (no LLM per reading)                       │
├──────────────────────────────────────────────────────────────────────────┤
│  demo_publisher / dashboard twins  →  raw  dc/<Hub|DC1|DC2>/v1/raw/...   │
│         │                                                                 │
│         ▼                                                                 │
│   deadband  →  sketch  →  anomaly  (+ chart-writer → chart_data.db)     │
│         │          │           │                                            │
│         └──────────┴───────────┴── MQTT: dc/<site>/v1/pipeline/*         │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│              CONTROL PLANE (LLM on demand + automation)                   │
├──────────────────────────────────────────────────────────────────────────┤
│  SAM Web UI (:8000)  ·  FleetQueryAgent  ·  MqttOrchestratorAgent         │
│  fleet-analysis gateway  ←  sensors/fleet/analysis-request (MQTT)       │
│  Optional: Slack gateway (@bot)  ·  analysis-to-slack bridge              │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Quick start (local laptop)

### Prerequisites

- Python 3.11+
- Solace PubSub+ (Cloud or on-prem)
- LLM endpoint (LiteLLM proxy, Azure OpenAI, etc.)

### 1. Setup

```bash
cd sam
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# SAM plugins used by this branch (also baked into the AWS image)
sam plugin add fleet-analysis --plugin sam-event-mesh-gateway
sam plugin add slack --plugin sam-slack-gateway-adapter

cp .env.example .env
# Edit: SOLACE_*, LLM_SERVICE_*, NAMESPACE
```

**LLM note:** For SAM, use `litellm_proxy/azure-gpt-5-mini` (or your proxy model name) in `LLM_SERVICE_GENERAL_MODEL_NAME` — not a bare provider string.

### 2. Traffic pipeline (MQTT microservices)

Equivalent to `start_traffic_generation.sh` on the laptop (same processes as EC2 Compose `deadband`, `sketch`, `chart-writer`, `demo-publisher`):

```bash
cd sam
./start_traffic_generation.sh
```

Topics (default site **`Hub`**): `dc/Hub/v1/raw/...`, `dc/Hub/v1/pipeline/filtered`, `sketched`, `alerts`.

### 3. SAM + anomaly + optional Slack

```bash
./start_demo_stack.sh
# chart-query :8010, SAM Web UI :8000, fleet-analysis gateway, anomaly, analysis→Slack
```

Health check:

```bash
./healthcheck_demo_stack.sh
curl -sf http://127.0.0.1:8010/health
```

### 4. Live pipeline dashboard (browser)

Open **`sam/demo_dashboard.html`** in a browser (file URL or static host), or host it on **Apache on EC2** (see [deploy/aws/README.md](deploy/aws/README.md#live-pipeline-dashboard-on-apache-optional)). Connect with your Solace **WebSocket** host/port and credentials.

| Tab | Data source |
|-----|-------------|
| Pipeline · 3 columns | Live MQTT (session counters) |
| 2D digital twin | MQTT + **chart-query** trends (`/series`) |
| **Fleet chat (SAM)** | iframe → **SAM Web UI gateway** (`:8000` or `/sam/` behind Apache) |

Configure **Chart API** and **SAM Web UI** in the header (saved to `localStorage`), or use `?chartQuery=` and `?samWebui=` query params. When served over HTTP(S) from a host, defaults are `http://<host>/charts` and `http://<host>:8000`.

For a frictionless EC2 demo, generate **`demo_dashboard.config.json`** from `.env` (see [deploy/aws/README.md](deploy/aws/README.md)) — the page auto-connects to Solace on load.

**Tip:** With EC2 `demo-publisher` running, disconnect the dashboard or disable continuous twin publish to avoid duplicate traffic.

### 5. Query via SAM Web UI

http://localhost:8000 — e.g. “What’s fleet status?”, “Plot m3-temp-motor for the last hour.”

---

## AWS deployment (EC2 + ECR)

Full steps: **[deploy/aws/README.md](deploy/aws/README.md)**.

```bash
# Laptop: build & push
./deploy/aws/scripts/build-image.sh mqtt5sr-demo:latest
AWS_REGION=us-east-2 ECR_REPOSITORY=hvac/fleet/management ./deploy/aws/scripts/push-images-ecr.sh

# EC2: pull & run (from repo root on the instance)
cp deploy/aws/env.deploy.example deploy/aws/.env   # edit secrets
./deploy/aws/scripts/init-data-dir.sh
export MQTT5SR_IMAGE=<your-ecr-uri>:latest
ENV_FILE=.env docker compose -f deploy/aws/docker-compose.yml up -d

# Optional Slack + Java sample
ENV_FILE=.env docker compose -f deploy/aws/docker-compose.yml --profile slack up -d
ENV_FILE=.env docker compose -f deploy/aws/docker-compose.yml --profile java up -d
```

### What runs in the cloud (default compose)

| Service | Port | Role |
|---------|------|------|
| `deadband`, `sketch`, `chart-writer`, `demo-publisher` | — | Same pipeline as `start_traffic_generation.sh` |
| `chart-query` | **8010** | Time-series / Plotly API (`chart_data.db`) |
| `sam-control-plane` | **8000** | Web UI + orchestrator + fleet query + **fleet-analysis gateway** |
| `anomaly` | — | Rule alerts + fleet status + triggers `sensors/fleet/analysis-request` |
| **Profile `slack`** | — | `slack-gateway` (Socket Mode @bot) + `analysis-to-slack` |

**Not deployed to EC2:** `sam/demo_dashboard.html` (local demo UI only).

Image must include `sam-event-mesh-gateway`, `sam-slack-gateway-adapter`, and `slack_sdk` (see `sam/requirements.txt`). Verify after build:

```bash
./deploy/aws/scripts/verify-python-image.sh mqtt5sr-demo:latest
```

---

## Slack

Three paths (all need `SLACK_BOT_TOKEN` in `.env`; gateway also needs `SLACK_APP_TOKEN`):

| Path | Mechanism |
|------|-----------|
| Per-sensor / fleet status cards | `anomaly` → `slack_notifier.py` (direct API) |
| Long **Automated Fleet Analysis** | `FLEET_CRITICAL` → MQTT `analysis-request` → fleet-analysis gateway → `analysis-response` → `analysis-to-slack` |
| Interactive | `slack-gateway` — mention the bot in an invited channel |

On EC2, enable the Compose profile:

```bash
ENV_FILE=.env docker compose -f deploy/aws/docker-compose.yml --profile slack up -d
```

Invite the bot to `SLACK_ALERT_CHANNEL`. Repeated CRITICALs are deduped (`SLACK_SENSOR_DEDUPE_SECONDS`, fleet interval `FLEET_SLACK_MIN_INTERVAL_SECONDS`).

---

## Fleet automation (MQTT)

When ≥50% of active sensors are **CRITICAL**, `anomaly_service` publishes to **`sensors/fleet/analysis-request`**. The **fleet-analysis gateway** (`sam-event-mesh-gateway`) routes to **FleetQueryAgent** and publishes **`sensors/fleet/analysis-response`** as JSON (`task_response` with report `text` plus SAM token fields on `a2a_task_response.metadata`). **`analysis_response_to_slack`** posts the narrative and an LLM token-usage footer when usage is present.

Config: `sam/configs/gateways/fleet-analysis-gateway.yaml` (`gateway_id`, `temporary_queue` for Solace exclusive-queue restarts).

**Production / token tuning:** [docs/FLEET_ANALYSIS_PRODUCTION.md](docs/FLEET_ANALYSIS_PRODUCTION.md) (checklist, `FLEET_*` env vars, A/B order).

**EC2:** Prefer fleet analysis **inside** `sam-control-plane` only. A separate `fleet-analysis-gateway` container (profile `fleet-analysis-standalone`) can cause `MAX_CLIENTS_FOR_QUEUE` if two consumers bind the same gateway queue — stop laptop SAM or the duplicate container.

---

## Project structure

```
mqtt5SRDemo/
├── sam/
│   ├── src/
│   │   ├── deadband_service.py, sketch_service.py, chart_writer_service.py
│   │   ├── demo_publisher.py, anomaly_service.py
│   │   ├── chart_query_service.py      # HTTP :8010
│   │   ├── fleet_query_tools.py, fleet_alert_analyzer.py
│   │   ├── analysis_response_to_slack.py
│   │   └── slack_notifier.py
│   ├── configs/agents/                 # FleetQueryAgent, orchestrator
│   ├── configs/gateways/               # webui, slack-bot, fleet-analysis-gateway
│   ├── demo_dashboard.html             # Live MQTT pipeline UI (local)
│   ├── start_traffic_generation.sh     # Laptop pipeline
│   ├── start_demo_stack.sh             # Laptop SAM + anomaly + gateways
│   └── requirements.txt
├── deploy/aws/                         # Docker Compose, ECR, EC2 runbook
├── src/main/java/                      # Optional MQTT5 + Schema Registry sample
└── demo/                               # Architecture deck, demo script
```

---

## Query tools (FleetQueryAgent)

| Tool | Use case |
|------|----------|
| `get_fleet_status` | Fleet health summary |
| `get_incident_context` | Per-sensor window + sketches |
| `get_plotly_spec` | Chart spec / pinned URLs (uses chart-query) |
| `get_recent_alerts` | Alert history |
| `recommend_dispatch_technicians` | Mock CMMS dispatch (demo) |

---

## Topic reference (Hub default)

| Stage | Topic pattern |
|-------|----------------|
| Raw | `dc/Hub/v1/raw/{site}/…/{sensor}/supply_temp_c` |
| Filtered | `dc/Hub/v1/pipeline/filtered` |
| Sketched | `dc/Hub/v1/pipeline/sketched` |
| Alerts | `dc/Hub/v1/pipeline/alerts` |
| Fleet analysis | `sensors/fleet/analysis-request` / `analysis-response` |

Override site: `DC_BROKER_SITE=DC1` in `.env`. See [DC_TOPIC_VERSIONING_README.md](DC_TOPIC_VERSIONING_README.md).

---

## Cost pattern

| Approach | LLM calls/day | Rough cost |
|----------|---------------|------------|
| Every MQTT message → LLM | Millions | **$1k+/day** |
| This demo | Human queries + rare fleet automation | **cents/day** |

---

## Tests

A single end-to-end test exercises the deterministic pipeline (deadband → sketch → anomaly) with synthetic readings. No broker is required.

```bash
cd sam
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/
```

Expected: `1 passed`.

---

## Optional: Schema Registry (Java)

SERDES is **off by default** in `MqttConfig.java` for the demo (`MQTT_JSON_SERDES_ENABLED=false`). Java publisher/subscriber and EKS registry deploy are documented in [DEPLOYMENT.md](DEPLOYMENT.md).

---

## Troubleshooting

| Symptom | Check |
|---------|--------|
| No Slack posts | EC2: `--profile slack`; image has `slack_sdk`; `docker compose logs anomaly \| grep SlackNotifier` |
| Fleet gateway restart loop | Solace queue bind count; stop duplicate gateway + laptop SAM; see [deploy/aws/README.md](deploy/aws/README.md) |
| LLM errors in SAM | `litellm_proxy/...` model name; `sam/test_llm.py` |
| Dashboard trends empty | chart-query running; `CHART_QUERY_BASE_URL` points at host with `chart_data.db` |
| Pipeline columns quiet | EC2 `docker compose ps` for deadband/sketch/anomaly/demo-publisher |

---

## Documentation

| Document | Description |
|----------|-------------|
| [GitHub Pages — value story](https://mstobo.github.io/hvac-fleet-sam-demo/) | Two-page executive overview (enable Pages: branch `main`, folder `/docs`) |
| [deploy/aws/README.md](deploy/aws/README.md) | ECR, EC2 Compose, Slack profile, fleet gateway |
| [demo/DEMO_SCRIPT.md](demo/DEMO_SCRIPT.md) | Presenter runbook |
| [DC_TOPIC_VERSIONING_README.md](DC_TOPIC_VERSIONING_README.md) | Topic taxonomy |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Schema Registry on EKS |
| [BLOG_POST.md](BLOG_POST.md) | Technical deep-dive |

---

## Tech stack

| Component | Technology |
|-----------|------------|
| Event broker | Solace PubSub+ Cloud |
| Pipeline | Python, Paho MQTT |
| Charts / rollups | SQLite `chart_data.db`, FastAPI chart-query |
| AI | Solace Agent Mesh, LiteLLM-compatible LLM |
| Cloud | Docker, ECR, EC2 Compose |

---

## License

Demonstration and educational use. See repository for details.

## Author

Matt Stobo
