# MQTT5 IoT Analytics with AI-Powered Insights

**Reduce MQTT noise by 99% and enable intelligent fleet monitoring with Solace Agent Mesh**

---

## Overview

This project demonstrates an architecture pattern for connecting high-volume IoT sensor data to Large Language Models (LLMs) **without incurring massive AI costs**.

The key insight: **Don't send every sensor reading to an LLM. Pre-process deterministically, then let AI answer questions.**

### What This Demo Shows

1. **Deterministic Data Pipeline** - Filters noise, generates natural language summaries, detects anomalies—all without AI
2. **SQLite Time-Series Store** - Stores processed data for efficient querying
3. **AI Query Layer** - LLM-powered agent answers questions by reading pre-processed data
4. **Cost Reduction** - From $1,000+/day (naive approach) to ~$0.01/day

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    DATA PLANE (No LLM Cost)                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   MQTT Publisher (sensors)                                      │
│        │                                                        │
│        ▼                                                        │
│   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐       │
│   │   Deadband   │──▶│    Sketch    │──▶│   Anomaly    │       │
│   │    Filter    │   │  Generator   │   │  Detector    │       │
│   │  (70% noise  │   │  (NL summary │   │  (rule-based │       │
│   │   removed)   │   │  per event)  │   │   alerts)    │       │
│   └──────────────┘   └──────────────┘   └──────────────┘       │
│                             │                  │                │
│                             ▼                  ▼                │
│                     ┌─────────────────────────────┐            │
│                     │          SQLite             │            │
│                     │  readings │ sketches │ alerts│            │
│                     └─────────────────────────────┘            │
└─────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                 CONTROL PLANE (LLM - On Demand)                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   User: "What's been happening with the sensors?"              │
│        │                                                        │
│        ▼                                                        │
│   ┌──────────────┐         ┌─────────────────────────┐         │
│   │  SAM Agent   │────────▶│  Tools query SQLite     │         │
│   │  (with LLM)  │         │  get_sketches()         │         │
│   └──────────────┘         │  get_alerts()           │         │
│        │                   │  get_fleet_status()     │         │
│        ▼                   └─────────────────────────┘         │
│   "Correlated spike across all sensors at 15:24 UTC.           │
│    Pattern suggests shared cause—check HVAC logs."             │
└─────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- Solace PubSub+ broker (Cloud or local)
- LLM endpoint (Azure OpenAI, OpenAI, or compatible)

### 1. Setup Environment

```bash
cd sam
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env` and configure:

```bash
# Solace Broker
SOLACE_BROKER_URL="wss://your-broker.messaging.solace.cloud:443"
SOLACE_BROKER_VPN="your-vpn"
SOLACE_BROKER_USERNAME="your-username"
SOLACE_BROKER_PASSWORD="your-password"

# LLM Service
LLM_SERVICE_ENDPOINT="https://your-llm-endpoint"
LLM_SERVICE_API_KEY="your-api-key"
LLM_SERVICE_GENERAL_MODEL_NAME="openai/gpt-4"
```

### 3. Start the Data Plane (Pipeline)

```bash
# Terminal 1: Start the sensor simulator
export SOLACE_HOST="your-broker.messaging.solace.cloud"
export SOLACE_PORT="8883"
export SOLACE_USER="your-username"
export SOLACE_PASS="your-password"
export SOLACE_TLS="true"

python src/demo_publisher.py
```

```bash
# Terminal 2: Start the processing pipeline
python src/mock_pipeline.py
```

You should see output like:
```
[Deadband] 🔇 SUPPRESS sensor-001 | delta 0.8% < 2.0%
[Deadband] 🟢 FORWARD sensor-002 | 45.3°C | zone=NORMAL
[Sketch]   ✍️  sensor-002 | "sensor-002 recorded a 5.2% spike..."
[Anomaly]  💤 SKIP | sensor-002 zone=NORMAL
```

### 4. Start the Control Plane (SAM)

```bash
# Terminal 3: Start Solace Agent Mesh
sam run
```

### 5. Query Your Data

Open http://localhost:8000 and try:

- **"What's been happening with the sensors lately?"**
- **"Any critical alerts in the last 10 minutes?"**
- **"Tell me about sensor-001"**
- **"What's the fleet status?"**

---

## Project Structure

```
mqtt5SRDemo/
├── sam/                          # Solace Agent Mesh project
│   ├── src/
│   │   ├── demo_publisher.py     # Simulates sensor network
│   │   ├── mock_pipeline.py      # Deterministic processing pipeline
│   │   ├── sensor_db.py          # SQLite database operations
│   │   └── fleet_query_tools.py  # SAM agent query tools
│   ├── configs/
│   │   ├── agents/
│   │   │   ├── fleet_query_agent.yaml  # Query agent config
│   │   │   └── main_orchestrator.yaml  # Orchestrator config
│   │   └── shared_config.yaml
│   ├── sensor_data.db            # SQLite database (created at runtime)
│   └── .env                      # Environment configuration
├── BLOG_POST.md                  # Technical deep-dive
├── BLOG_POST_EXECUTIVE.md        # Business-focused overview
└── README.md                     # This file
```

---

## How It Works

### Stage 1: Deadband Filter

Suppresses readings that haven't changed significantly:

```python
DEADBAND_PCT = 0.02  # 2% threshold

if abs(new_value - last_value) / last_value < DEADBAND_PCT:
    return "suppress"  # ~70% of readings filtered
```

### Stage 2: Sketch Generator

Creates natural language summaries for significant readings:

```python
sketch = f"{sensor_id} recorded a {delta:.1f}% spike to {temp:.1f}°C. "
sketch += f"30s window: mean {mean:.1f}°C. Zone: {zone}."
# Output: "sensor-001 recorded a 38.4% spike to 65.8°C. Zone: CRITICAL."
```

### Stage 3: Anomaly Detector

Rule-based alert generation (no LLM):

```python
if temperature >= 65.0:
    insert_alert(sensor_id, "CRITICAL", "SPIKE", description)
elif temperature >= 58.0:
    insert_alert(sensor_id, "WARNING", "ELEVATED", description)
```

### Stage 4: AI Query Layer

LLM reads pre-computed summaries and synthesizes insights:

```
User: "What's been happening?"

AI reads sketches:
- "sensor-001: 38% spike to 65.8°C. CRITICAL."
- "sensor-002: 36% spike to 64.2°C. CRITICAL."
- "sensor-003: 41% spike to 66.1°C. CRITICAL."

AI synthesizes:
"Correlated spike across all three sensors at 15:24 UTC.
Pattern suggests shared cause—check HVAC/power logs.
Recommended: Investigate within 1 hour."
```

---

## Available Query Tools

The FleetQueryAgent has these tools for querying the SQLite database:

| Tool | Use Case | Example Question |
|------|----------|------------------|
| `get_sketches` | Activity summaries | "What's been happening?" |
| `get_recent_alerts` | Alert queries | "Any critical alerts?" |
| `get_alert_summary` | Alert statistics | "How many warnings today?" |
| `get_fleet_status` | Fleet health | "What's the fleet status?" |
| `get_sensor_details` | Per-sensor info | "Tell me about sensor-001" |
| `get_system_statistics` | Data metrics | "How much data processed?" |
| `acknowledge_alert` | Alert management | "Acknowledge alert 123" |

---

## Cost Analysis

| Approach | Messages/Day | LLM Calls/Day | Cost/Day |
|----------|--------------|---------------|----------|
| Every message → LLM | 4,320,000 | 4,320,000 | **$8,640** |
| Our pattern | 4,320,000 | ~50 (queries) | **$0.05** |

**99.99% cost reduction** by invoking AI only when humans ask questions.

---

## Configuration

### Pipeline Thresholds (mock_pipeline.py)

```python
DEADBAND_PCT = 0.02      # 2% change threshold
HEARTBEAT_SECS = 30.0    # Max silence before forced update
WARNING_TEMP = 58.0      # Warning zone threshold
CRITICAL_TEMP = 65.0     # Critical zone threshold
```

### Agent Instructions (fleet_query_agent.yaml)

The agent instructions guide tool selection:

```yaml
instruction: |
  TOOL SELECTION GUIDE:
  
  "What's been happening?" / "Recent activity?"
  → USE get_sketches FIRST
  
  "Any alerts?" / "Critical events?"
  → USE get_recent_alerts
```

---

## Optional: Schema Registry Integration

For production deployments requiring schema validation, this project also includes integration with Solace Schema Registry.

### What Schema Registry Adds

- **Schema Validation**: Validate sensor payloads against JSON Schema
- **Data Quality**: Reject malformed messages at the source
- **Schema Evolution**: Manage schema versions centrally
- **Interoperability**: SERDES support for Java clients

### Schema Registry Architecture

```
Publisher (Java) → MQTT5 Broker → Subscriber (Java)
      ↓                                  ↓
Schema Registry (AWS EKS) ←──────────────┘
      ↓
PostgreSQL (CloudNativePG)
```

### Schema Registry Quick Start

1. Deploy Schema Registry on AWS EKS (see [DEPLOYMENT.md](DEPLOYMENT.md))
2. Register your sensor schema:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "sensorId": { "type": "string" },
    "temperature": { "type": "number", "minimum": -50, "maximum": 150 },
    "timestamp": { "type": "string", "format": "date-time" }
  },
  "required": ["sensorId", "temperature", "timestamp"]
}
```

3. Configure Java clients in `MqttConfig.java`:

```java
public static final String SCHEMA_REGISTRY_URL = "https://your-registry";
public static final String SCHEMA_ARTIFACT_ID = "solace/samples/tempsensor";
```

4. Run Java publisher/subscriber:

```bash
mvn exec:java -Dexec.mainClass="MQTT5Subscriber"
mvn exec:java -Dexec.mainClass="MQTT5Publisher"
```

### Schema Registry Project Files

```
mqtt5SRDemo/
├── src/main/java/
│   ├── MQTT5Publisher.java    # Publisher with schema validation
│   ├── MQTT5Subscriber.java   # Subscriber with schema validation
│   ├── MqttConfig.java        # Configuration
│   └── SerdesSupport.java     # SERDES utilities
├── infra/
│   ├── eks-cluster.yaml       # AWS EKS CloudFormation
│   ├── schema-registry-ecr.yaml
│   └── values-override.yaml.example
├── DEPLOYMENT.md              # EKS deployment guide
└── pom.xml                    # Maven dependencies
```

For complete Schema Registry documentation, see [DEPLOYMENT.md](DEPLOYMENT.md).

---

## Documentation

| Document | Description |
|----------|-------------|
| [BLOG_POST.md](BLOG_POST.md) | Technical deep-dive into the architecture |
| [BLOG_POST_EXECUTIVE.md](BLOG_POST_EXECUTIVE.md) | Business-focused overview for architects/executives |
| [DEPLOYMENT.md](DEPLOYMENT.md) | AWS EKS deployment guide for Schema Registry |

---

## Troubleshooting

### Pipeline not receiving messages

```bash
# Check if publisher is connected
tail -f /tmp/publisher.log

# Verify broker credentials
echo $SOLACE_HOST $SOLACE_PORT $SOLACE_USER
```

### SAM agent not using correct tools

Check the agent instructions in `configs/agents/fleet_query_agent.yaml`. The LLM needs explicit guidance on when to use each tool.

### Database not populating

```bash
cd sam/src
python -c "import sensor_db; print(sensor_db.get_statistics())"
```

### LLM errors

Verify your `.env` configuration:
- `LLM_SERVICE_ENDPOINT` - Must be accessible
- `LLM_SERVICE_API_KEY` - Must be valid
- `LLM_SERVICE_GENERAL_MODEL_NAME` - Include provider prefix (e.g., `openai/gpt-4`)

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Event Broker | Solace PubSub+ Cloud |
| Pipeline | Python + Paho MQTT |
| Database | SQLite (demo) / TimescaleDB (production) |
| AI Framework | Solace Agent Mesh |
| LLM | Azure OpenAI / OpenAI / LiteLLM compatible |

---

## Contributing

Issues and pull requests are welcome!

## License

This project is provided as-is for demonstration and educational purposes.

## Author

Matt Stobo - [GitHub](https://github.com/mstobo)
