# Solace Agent Mesh — SAM-Native Demo Quickstart (VERIFIED)

> **Note:** This version reflects the **verified SAM architecture** based on
> actual source code from `SolaceLabs/solace-agent-mesh` and the
> `sam-event-mesh-agent` core plugin reference implementation.

---

## ⚠️ Key Architecture Corrections (from Previous Version)

The previous agent files contained incorrect patterns. Here is what was wrong
and what the real SAM architecture looks like:

| Aspect | ❌ Previous (Wrong) | ✅ Corrected (Verified) |
|---|---|---|
| **Base class** | `class DeadbandAgent(BaseAgent)` | No custom class — use plain Python `async def` functions |
| **Imports** | `from google.adk.agents import BaseAgent` | `from google.adk.tools import ToolContext` |
| **SAM SDK** | `import solace_agent_mesh.common as sam` | No such module — config via `tool_config` dict |
| **Pipeline** | `yield Event(actions=EventActions(...))` | LLM instruction + `inter_agent_communication` allow_list |
| **Tool type** | `agent_type: action` (fake) | `tool_type: python` with `component_module` + `function_name` |
| **App module** | Not specified | `app_module: solace_agent_mesh.agent.sac.app` ✅ |
| **LLM access** | `ctx.llm_client.generate(prompt)` | LLM is the agent itself — called by SAM orchestrator |
| **Zone routing** | Declarative YAML `routing: NORMAL: action: skip` | LLM instruction: "if zone is NORMAL, do not call LLM" |

---

## How SAM Agents Actually Work

```
┌─────────────────────────────────────────────┐
│            SAM Agent (YAML config)           │
│  app_module: solace_agent_mesh.agent.sac.app │
│                                              │
│  LlmAgent (Google ADK)                       │
│    └── instruction: "You are..."             │
│    └── tools:                                │
│          tool_type: python                   │
│          component_module: my_tools          │  ← Your .py file
│          function_name: my_tool_function     │  ← Your async def
│                                              │
│  inter_agent_communication:                  │
│    allow_list: ["NextAgent"]                 │  ← Agent chaining
└─────────────────────────────────────────────┘
```

**Key insight:** You write **plain Python async functions**. SAM wraps them
as Google ADK `FunctionTool` instances and gives the LLM access to them.
The LLM decides when to call them based on the `instruction`.

For deterministic pipelines (like sensor processing), write tight instructions
that tell the LLM exactly when to call each tool and what to do with the result.

---

## Project Setup

```bash
# Install SAM
pip install solace-agent-mesh

# Install demo publisher
pip install paho-mqtt

# Initialize SAM project
sam init sensor-demo
cd sensor-demo
```

---

## File Placement

```
sensor-demo/
├── configs/
│   └── agents/
│       ├── deadband_agent_sam.yaml     ← Noise filter agent
│       ├── sketch_agent_sam.yaml       ← NL summarizer agent
│       ├── fleet_agent_sam.yaml        ← Fleet anomaly agent
│       └── sensor_pipeline_sam.yaml    ← Pipeline reference
├── src/
│   ├── deadband_agent_sam.py           ← apply_deadband_filter tool
│   ├── sketch_agent_sam.py             ← generate_sketch tool
│   └── fleet_agent_sam.py              ← analyze_fleet tool
└── demo_publisher.py                   ← Run separately
```

---

## Environment Variables

```bash
export SOLACE_BROKER_URL="ws://YOUR_BROKER.messaging.solace.cloud:8080"
export SOLACE_BROKER_USERNAME="YOUR_USERNAME"
export SOLACE_BROKER_PASSWORD="YOUR_PASSWORD"
export SOLACE_BROKER_VPN="YOUR_VPN"
export NAMESPACE="sensors/"
export LLM_SERVICE_ENDPOINT="https://api.openai.com/v1"
export LLM_SERVICE_API_KEY="sk-..."
export LLM_SERVICE_GENERAL_MODEL_NAME="gpt-4o-mini"
```

---

## Running the Demo

```bash
# From sam/ — pipeline microservices + publisher (see start_demo_stack.sh)
./start_traffic_generation.sh

# Or manually:
# Terminal 1 — deadband → sketch → anomaly (writes sensor_data.db + telemetry_* tables)
# Terminal 2 — demo publisher (15 telemetry points: 3 assets × 5 metrics)
cd sam && PYTHONPATH=src python3 src/demo_publisher.py

# SAM agents (FleetQueryAgent reads telemetry_* when populated)
sam run

# Browser — dashboard
open demo_dashboard.html
```

### Multi-metric telemetry (2026 branch)

Each cooling asset publishes **five** signals:

| Metric | Example point id |
|--------|------------------|
| `inlet_temp_c` | `m1-temp-inlet` |
| `outlet_temp_c` | `m1-temp-outlet` |
| `motor_temp_c` | `m1-temp-motor` |
| `humidity_rh` | `m1-humidity` |
| `motor_vibration_mm_s` | `m1-vibration` |

**Publish modes** (`demo_publisher.py`):

- `DEMO_PUBLISH_MODE=topics` (default) — one MQTT topic per metric:  
  `dc/Hub/v1/raw/.../machine-001/humidity_rh`
- `DEMO_PUBLISH_MODE=bundle` — legacy gateway JSON on `.../machine-001/_bundle` (`dc.raw.bundle.v1`)

**Database:** Pipeline dual-writes legacy tables (`sensor_readings`, …) and canonical  
`telemetry_*` tables. `FleetQueryAgent` tools prefer `telemetry_*` when data exists  
(`FLEET_QUERY_USE_TELEMETRY=true`, default).

**Config:** Per-metric deadband and thresholds in `configs/domains/hvac/metrics.json`.  
Override DB path with `SENSOR_DB_PATH`.

---

## How Agent Chaining Works in SAM

Agents chain via the **A2A protocol** over the Solace broker:

1. `DeadbandAgent` has `allow_list: ["SketchAgent"]` — it can delegate to SketchAgent
2. `SketchAgent` has `allow_list: ["AnomalyAgent"]` — it can delegate to AnomalyAgent
3. `AnomalyAgent` has `allow_list: []` — terminal agent for per-sensor alerts
4. `FleetAnomalyAgent` subscribes to sketch output in parallel — detects fleet-wide patterns

The Solace broker handles the A2A message routing between agents automatically.

---

## Fleet Anomaly Detection

The `FleetAnomalyAgent` monitors all sensor sketches to detect patterns that
individual sensors would miss:

| Pattern | Description | Example |
|---------|-------------|---------|
| **correlated_drift** | Majority of sensors trending same direction | HVAC failure — all sensors creeping upward |
| **simultaneous_escalation** | Multiple sensors changing zone together | Power event affecting entire rack |
| **sensor_silence** | Multiple sensors stop reporting | Network switch failure |
| **zone_clustering** | Many sensors in WARNING/CRITICAL | Environmental incident |

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Pipeline Architecture                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   MQTT Gateway                                                       │
│       │                                                              │
│       ▼                                                              │
│   ┌─────────────┐    suppress    ┌──────────────────┐               │
│   │  Deadband   │ ──────────────▶│  /suppressed     │               │
│   │   Agent     │                │  (audit trail)   │               │
│   └──────┬──────┘                └──────────────────┘               │
│          │ forward                                                   │
│          ▼                                                           │
│   ┌─────────────┐                                                   │
│   │   Sketch    │                                                   │
│   │   Agent     │                                                   │
│   └──────┬──────┘                                                   │
│          │                                                           │
│          ├─────────────────────────────────┐                        │
│          │                                 │                        │
│          ▼                                 ▼                        │
│   ┌─────────────┐                   ┌─────────────┐                 │
│   │  Anomaly    │   (per-sensor)    │   Fleet     │  (fleet-wide)  │
│   │   Agent     │                   │   Agent     │                 │
│   └──────┬──────┘                   └──────┬──────┘                 │
│          │                                 │                        │
│          ▼                                 ▼                        │
│   ┌──────────────┐                 ┌──────────────┐                 │
│   │ /alerts/     │                 │ /alerts/     │                 │
│   │  active      │                 │  fleet       │                 │
│   └──────────────┘                 └──────────────┘                 │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## SAM Resources (Official)

| Resource | URL |
|---|---|
| SAM GitHub | github.com/SolaceLabs/solace-agent-mesh |
| Core Plugins | github.com/SolaceLabs/solace-agent-mesh-core-plugins |
| SAM Docs | solacelabs.github.io/solace-agent-mesh |
| A2A Protocol | google.github.io/A2A |

---

## File Summary

| File | Type | Role |
|---|---|---|
| `demo_publisher.py` | Python | MQTT5 sensor simulator |
| `deadband_agent_sam.py` | Python | `apply_deadband_filter` tool function |
| `deadband_agent_sam.yaml` | YAML | Deadband filter agent config |
| `sketch_agent_sam.py` | Python | `generate_sketch` tool function |
| `sketch_agent_sam.yaml` | YAML | Sketch summarizer agent config |
| `fleet_agent_sam.py` | Python | `analyze_fleet` tool function |
| `fleet_agent_sam.yaml` | YAML | Fleet anomaly agent config |
| `demo_dashboard.html` | HTML | Live browser visualization |
| `sensor_pipeline_sam.yaml` | YAML | Pipeline architecture reference |

---

*Solace Agent Mesh POC Demo — Matthew Stobo, Senior Solutions Engineer*
*Agent files verified against SolaceLabs/solace-agent-mesh source — April 2026*
