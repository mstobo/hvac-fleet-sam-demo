# Fleet analysis — production & token tuning

Operational guide for **automated fleet analysis** (`FLEET_CRITICAL` → `sensors/fleet/analysis-request` → SAM → `analysis-response` → Slack). Narrative and economics are in the [architecture blog](blog/reason-on-the-exception.html).

**Related docs**

| Doc | Use |
|-----|-----|
| [deploy/aws/README.md](../deploy/aws/README.md) | EC2 Compose, ECR, sketch toggle, Apache |
| [deploy/aws/env.deploy.example](../deploy/aws/env.deploy.example) | Env template (copy to `.env`) |
| [README.md](../README.md) | Fleet automation overview, tools table |
| [demo/DEMO_SCRIPT.md](../demo/DEMO_SCRIPT.md) | Live presenter flow |
| [tools/sketch-token-lab/README.md](../tools/sketch-token-lab/README.md) | Offline NL vs jargon token estimates |

---

## Architecture (one paragraph)

Deterministic pipeline (**deadband → sketch → anomaly**) writes rollups to SQLite. **No LLM on the hot path.** When fleet rules declare **FLEET_CRITICAL**, `fleet_alert_analyzer` debounces and publishes one `analysis-request`. The **fleet-analysis gateway** invokes **FleetQueryAgent** with a **SECTION A tool budget**, then publishes `task_response` JSON. **`analysis_response_to_slack`** posts report text and an LLM usage footer when metadata is present.

Config: `sam/configs/gateways/fleet-analysis-gateway.yaml`, `sam/configs/agents/fleet_query_agent.yaml`.

**EC2:** Run fleet analysis **inside `sam-control-plane` only**. The Compose profile `fleet-analysis-standalone` is a second consumer on the same gateway queue and can cause `MAX_CLIENTS_FOR_QUEUE` if both run.

---

## Production principles

1. **Keep generative AI off the IIoT hot path** — deadband + heartbeat on the stream; never LLM per reading.
2. **Agents read curated state** — sketches and telemetry in SQLite; chart-query for URLs; not raw topic firehose.
3. **Tool budgets on automation** — SECTION A below; no nine-point forensic loops on fleet triggers.
4. **Measure one knob at a time** — compare Slack `LLM usage` footers (or `a2a_task_response.metadata`) after each change.

---

## Production checklist

| # | Item | Action |
|---|------|--------|
| 1 | Hot path | Pipeline services up (`deadband`, `sketch`, `anomaly`, `chart-writer`); `SENSOR_DB_PATH` on shared volume |
| 2 | Fleet trigger | `FLEET_CRITICAL_FRACTION` (default 0.5); `anomaly_service` + debounce in `fleet_alert_analyzer` |
| 3 | SAM + gateway | `sam-control-plane` includes fleet-analysis gateway; LiteLLM model names use `litellm_proxy/...` |
| 4 | Public chart URLs | `CHART_PUBLIC_BASE_URL` or `DASHBOARD_PUBLIC_HOST` so Slack/browser can open `machine-plotly-html` links |
| 5 | Debug off (automation) | `FLEET_QUERY_DEBUG_SKETCH_EVIDENCE=false` — avoids verbose `debug.section_7_lines` in tool JSON |
| 6 | Lean tool payloads | `FLEET_MACHINE_PLOTLY_INCLUDE_SPEC=false` unless debugging Plotly spec size |
| 7 | Token tuning | Follow [A/B order](#token-tuning-ab-order) below; restart `sam-control-plane` after env changes |
| 8 | Verify report shape | Sections 1–8, three `machine-plotly-html` links, one section-7 sketch summary, one dispatch block |

**Local laptop note:** `sam/start_demo_stack.sh` and `sam/run_sam_control_plane.sh` default `FLEET_QUERY_DEBUG_SKETCH_EVIDENCE=true` for troubleshooting. Override in `sam/.env` or export `false` before starting if you are testing fleet automation token use locally.

---

## SECTION A tool budget (automated fleet)

Applies when the gateway message selects **SECTION A** (`FLEET_CRITICAL_ANALYSIS_REQUEST` + `fleet_status` FLEET_CRITICAL). Enforced in `fleet-analysis-gateway.yaml` input expression and `fleet_query_agent.yaml`.

| Tool | Limit |
|------|-------|
| `get_incident_context` | **1× per machine** (`machine-001`, `machine-002`, `machine-003`), `minutes=120` — **not** per inlet/motor/outlet point |
| `get_machine_plotly_spec` | **1× per machine**, same window, `value_key=avg_v` — only chart tool allowed |
| `get_plotly_spec`, `get_chart_series`, `get_sensor_details`, `get_sketches`, `get_recent_alerts` | **Forbidden** unless user payload explicitly asks |
| `recommend_dispatch_technicians` | **≤1** (single hottest point; top 3 techs once in section 8) |

**Report shape**

- Start with `1) Summary`; Chart Evidence = exactly **three** `machine-plotly-html` URLs (one per machine).
- Section 7: one bullet `Sketch evidence (by machine): machine-001=N, …` from `statistics.sketch_count`.
- No chart URLs under `###` headings; no trailing “Final note” only.

Post-process (in `analysis_response_to_slack` / `fleet_analysis_response.py`): collapse duplicate sketch lines; flag incomplete structure before Slack.

---

## Environment variables

Set in `deploy/aws/.env` (from `env.deploy.example`). Restart **sam-control-plane** (and **sketch** if changing sketch style) after changes.

### Token tuning

| Variable | Default | Purpose |
|----------|---------|---------|
| `FLEET_INCIDENT_CONTEXT_SKETCH_LIMIT` | `10` | Sketch cap for `get_incident_context("machine-00x", …)` |
| `INCIDENT_CONTEXT_SKETCH_LIMIT` | `25` | Sketch cap for point-level / user deep-dives (`machine-003:motor_temp_c`, `m3-temp-motor`) |
| `FLEET_QUERY_DEBUG_SKETCH_EVIDENCE` | `false` (EC2) | Adds `debug.*` to incident context JSON — keep **false** on automation |
| `FLEET_QUERY_SKETCH_SECTION7_VERBOSE` | `false` | When debug on: two `section_7_lines` per call instead of one |
| `FLEET_MACHINE_PLOTLY_INCLUDE_SPEC` | `false` | Include full `plotly_spec` in machine chart tool JSON |
| `MAX_TOKENS` | *(unset)* | Optional cap on `general_model` in `sam/configs/shared_config.yaml` — completion safety rail |

### Sketch style (input size)

| Variable | Purpose |
|----------|---------|
| `SKETCH_STYLE` | `nl` or `jargon` in env for new sketches from `sketch_service` |
| `SKETCH_STYLE_OVERRIDE_PATH` | Runtime override file on dbdata volume (dashboard NL/Jargon or `set-sketch-style.sh`) |

```bash
./deploy/aws/scripts/set-sketch-style.sh jargon deploy/aws/.env
```

### Charts & Slack

| Variable | Purpose |
|----------|---------|
| `CHART_PUBLIC_BASE_URL` | Reachable base for pinned Plotly HTML (Slack/browser) |
| `DASHBOARD_PUBLIC_HOST` | Alternative: derives `http://<host>/charts` |
| `CHART_QUERY_BASE_URL` | Internal service URL (Compose: `http://chart-query:8010`) |
| `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_ALERT_CHANNEL` | Profile `slack` for analysis-to-slack bridge |

### Fleet automation

| Variable | Purpose |
|----------|---------|
| `FLEET_CRITICAL_FRACTION` | Fraction of active sensors in CRITICAL to declare FLEET_CRITICAL (default `0.5`) |
| `ANALYSIS_DEBOUNCE_SECONDS` | Debounce before publishing `analysis-request` |
| `FLEET_ANALYSIS_GATEWAY_ID` | Gateway id (avoid queue clashes between laptops and EC2) |
| `USE_TEMPORARY_QUEUES` | `true` recommended on Solace Cloud restarts |

---

## Token tuning A/B order

Change **one** variable per fleet-critical test; note Slack footer **prompt / completion / total** and trace id (`fa-…`).

1. **`FLEET_INCIDENT_CONTEXT_SKETCH_LIMIT`** (e.g. 10 vs 25) — largest repeatable tool JSON shrink for SECTION A.
2. **`MAX_TOKENS`** on `*general_model*` — caps runaway completion; does not fix huge tool returns.
3. **`SKETCH_STYLE=jargon`** (or dashboard toggle) — smaller sketch text in DB and tool JSON (~30% total reduction observed vs NL in one demo stack).
4. **`FLEET_QUERY_DEBUG_SKETCH_EVIDENCE=false`** — prevents model from pasting debug lines into section 7.

Offline sketch-only estimates (not full SAM run):

```bash
cd tools/sketch-token-lab && pip install -r requirements.txt
python compare.py --sketches 10 --machines 3
python compare.py --sketches 25 --machines 3
```

---

## Verification

1. Trigger **FLEET_CRITICAL** (dashboard preset or `FLEET_CRITICAL_FRACTION` tuning — see [demo/DEMO_SCRIPT.md](../demo/DEMO_SCRIPT.md)).
2. Wait for debounce (~60s default) then **Automated Fleet Analysis** in Slack.
3. Confirm:
   - Sections **1)–8)** present; **3** `machine-plotly-html` links open from your network.
   - Section 7 counts only; no wall of per-point sketch lines.
   - Footer shows token totals when SAM stamps metadata.
4. In tool JSON (WebUI or logs): `statistics.sketch_limit`, `statistics.sketch_count`, `sketches_at_limit`.

**Logs:** `/tmp/sam-fleet-analysis-gateway.log`, `/tmp/sam-control-plane.log`, `/tmp/analysis-response-to-slack.log` (laptop); `docker compose logs sam-control-plane` (EC2).

---

## Replay & incident snapshots (planned pattern)

Broker **replay** does not reduce tokens unless you **republish a stored report** instead of re-running SAM. For audit/demo replay:

- Store `analysis-response` keyed by `correlation_id` (`generated_at`, incident window, report text, chart URLs, context fingerprint).
- On duplicate replay, republish JSON — skip LLM.

Not fully automated in this repo yet; design note for production hardening.

---

## Quick commands (EC2)

```bash
cd deploy/aws
ENV_FILE=.env docker compose -f docker-compose.yml --profile slack up -d
docker compose logs -f sam-control-plane
./scripts/set-sketch-style.sh jargon .env
```

Data plane DB on host: `deploy/aws/data/sensor_data.db` (set `DATA_DIR=./deploy/aws/data` when running tools locally against the same file).
