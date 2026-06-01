# Bring your own Slack (BYO)

The **public live demo** (shared EC2 dashboard) runs **without Slack**—we do not put visitor or corporate tokens on a shared host. You still see the same architecture in the browser (**Pipeline · 3 columns** + **Fleet chat (SAM)**).

To receive **fleet alerts**, **Automated Fleet Analysis** reports (sections 1–8, chart links, token/cost footer), and **@bot** queries in **your** workspace, run **your own copy** of the stack with **your** Slack app tokens.

---

## Public demo vs BYO Slack

| | Public EC2 demo | Your deploy (BYO Slack) |
|--|-----------------|-------------------------|
| Dashboard + SAM chat | ✓ Live | ✓ (local or your host) |
| Slack delivery | ✗ (use screenshots / blog for narrative) | ✓ Your channel |
| Where tokens live | N/A | **Your** `.env` only—never on shared EC2 |

Slack is a **fan-out gateway** on the same MQTT analysis path—not a separate product integration.

---

## What you get in Slack (three paths)

| Path | When | What appears |
|------|------|----------------|
| **Deterministic alerts** | Per-sensor CRITICAL, fleet status | Short status cards (`anomaly` → `slack_notifier`) |
| **Automated Fleet Analysis** | `FLEET_CRITICAL` after debounce | Long report + chart URLs + LLM token/cost footer (`analysis-response` → `analysis-to-slack`) |
| **Interactive** | `@YourBot …` | SAM answers via `slack-gateway` → FleetQueryAgent |

---

## 1. Create a Slack app (your workspace)

1. [https://api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → From scratch.
2. **Socket Mode** → ON → create an **App-Level Token** (`xapp-…`, scope `connections:write`).
3. **OAuth & Permissions** → **Bot Token Scopes**:
   - `app_mentions:read`, `chat:write`
   - `channels:history`, `groups:history`, `im:history`
   - `users:read`, `users:read.email`
   - Optional: `files:read`, `files:write`
4. **Event Subscriptions** → ON → subscribe to:
   - `app_mention`, `message.channels`, `message.groups`, `message.im`
5. **Install to workspace** → copy **Bot User OAuth Token** (`xoxb-…`).
6. Create a channel (e.g. `#hvac-fleet-demo`) and **invite the bot**.

More detail in [`sam/configs/gateways/slack-bot.yaml`](../sam/configs/gateways/slack-bot.yaml).

---

## 2. Configure environment

Set in **`sam/.env`** (laptop) or **`deploy/aws/.env`** (Docker on your host):

```bash
SLACK_BOT_TOKEN="xoxb-..."
SLACK_APP_TOKEN="xapp-..."
SLACK_ALERT_CHANNEL="#hvac-fleet-demo"
```

Also required (same as any deploy): **Solace** (`SOLACE_*`), **LLM** (`LLM_SERVICE_*`), **`NAMESPACE`**.

Optional tuning:

```bash
ANALYSIS_DEBOUNCE_SECONDS=20          # shorter wait before auto-analysis (demo rooms)
FLEET_CRITICAL_FRACTION=0.34          # easier to hit fleet-critical (~3/9 points)
FLEET_SLACK_MIN_INTERVAL_SECONDS=180  # throttle repeat fleet Slack cards
SLACK_RATE_LIMIT_SECONDS=60           # per-sensor alert dedupe
```

If you share a Solace VPN with another SAM stack, set unique gateway ids:

```bash
SLACK_GATEWAY_ID=slack-gw-yourname
FLEET_ANALYSIS_GATEWAY_ID=fleet-analysis-gw-yourname
USE_TEMPORARY_QUEUES=true
```

Chart links in Slack need a browser-reachable base:

```bash
CHART_PUBLIC_BASE_URL="http://<your-host>/charts"   # Apache proxy to chart-query
# or CHART_PUBLIC_BASE_URL="http://<your-host>:8010"
```

---

## 3. Start the stack

### Option A — Local laptop

```bash
cd sam
cp .env.example .env    # then edit tokens + broker + LLM
./setup_venv.sh && source .venv/bin/activate
pip install -r requirements.txt
sam plugin add slack --plugin sam-slack-gateway-adapter   # once, if needed

./start_traffic_generation.sh   # pipeline + demo publisher (or use dashboard twin only)
./start_demo_stack.sh           # SAM, gateways, anomaly, analysis-to-slack
./healthcheck_demo_stack.sh
```

Logs: `/tmp/sam-slack-gateway.log`, `/tmp/analysis-response-to-slack.log`, `/tmp/anomaly-service.log`.

### Option B — Docker Compose (your EC2 / private host)

```bash
cp deploy/aws/env.deploy.example deploy/aws/.env   # edit Slack + broker + LLM
./deploy/aws/scripts/init-data-dir.sh

ENV_FILE=.env docker compose -f deploy/aws/docker-compose.yml --profile slack up -d
```

Fleet analysis runs **inside** `sam-control-plane` on AWS layout; the `slack` profile adds `slack-gateway` + `analysis-to-slack`.

---

## 4. Verify

1. **Invite bot** to `SLACK_ALERT_CHANNEL`.
2. Open dashboard → **2D digital twin** → **FLEET_CRITICAL preset** (or wait for simulator).
3. Expect:
   - **T+0:** short **FLEET STATUS** card (deterministic).
   - **T+debounce:** one **Automated Fleet Analysis** message (sections 1–8, three `machine-plotly-html` links, token footer).
4. Test interactive: `@YourBot What is the current fleet status?`

Faster smoke test (skip debounce): from `sam/` with venv active:

```bash
python src/fleet_alert_analyzer.py --now
```

---

## 5. Troubleshooting

| Symptom | Check |
|---------|--------|
| No Slack at all | Tokens in `.env`; bot invited to channel; `slack-gateway` / `analysis-to-slack` running |
| Fleet card but no long analysis | Debounce (`ANALYSIS_DEBOUNCE_SECONDS`); rate limit (`ANALYSIS_RATE_LIMIT_SECONDS`); `ENABLE_AUTO_ANALYSIS=true`; SAM / LLM healthy |
| @bot silent | Socket Mode + app token; event subscriptions; `docker compose logs slack-gateway` or `/tmp/sam-slack-gateway.log` |
| Chart links broken in report | `CHART_PUBLIC_BASE_URL` / `CHART_QUERY_API_KEY`; chart-query reachable from browser |
| Queue / gateway clash on shared broker | Unique `SLACK_GATEWAY_ID`; stop duplicate SAM stacks on same VPN |

---

## Presenter note (video / main stage)

- **Live:** dashboard + SAM Fleet chat.
- **Slack proof:** screenshot of a real Automated Fleet Analysis (internal run)—same MQTT `analysis-response`, different gateway.
- **CTA for builders:** “Want this in your Slack? → [`demo/SLACK_BYO.md`](SLACK_BYO.md)”

---

## See also

- [README § Slack](../README.md#slack)
- [deploy/aws/README.md](../deploy/aws/README.md)
- [DEMO_SCRIPT.md](DEMO_SCRIPT.md) — live presentation flow
- [FLEET_ANALYSIS_PRODUCTION.md](../docs/FLEET_ANALYSIS_PRODUCTION.md) — token tuning, SECTION A budgets
