# Why Your IIoT AI Project Will Fail (And How to Fix It)

**The architecture mistake burning through enterprise AI budgets — and the pattern that cuts costs by 99%**

---

> 📖 **Part 2 of 2**
> - **Part 1 (Business case):** [The Token Burn Problem](BLOG_POST_EXECUTIVE.md)
> - **Part 2 (this post):** implementation and practical lessons

---

## The Problem in One Line

If your architecture sends every telemetry event to an LLM, cost and reliability become tied to message volume.

The scalable alternative is:
- deterministic event refinement in the **data plane**
- AI reasoning in the **query plane** (on-demand, human-triggered)

---

## Reference Architecture

```text
Telemetry -> Deadband -> Sketch -> Anomaly -> Alerts/Store
                                 \
                                  -> Query Plane (SAM + LLM on demand)
```

### Why this works

- High-volume processing stays cheap and deterministic.
- AI sees curated context instead of noisy raw streams.
- The same events fan out to dashboards, alerts, and query consumers.

---

## What We Built

### Data plane services

1. `deadband_service.py`  
   Suppresses statistically insignificant changes and emits only material updates.

2. `sketch_service.py`  
   Converts filtered events into compact natural-language "sketches" for downstream reasoning.

3. `anomaly_service.py`  
   Applies deterministic rules for zone/severity and incident lifecycle signaling.

### Core topic flow

| Topic | Publisher | Subscriber | Purpose |
|---|---|---|---|
| `sensors/temperature/#` | sensors | deadband service | raw telemetry |
| `sensors/pipeline/filtered` | deadband service | sketch service | significant updates |
| `sensors/pipeline/sketched` | sketch service | anomaly service | enriched events |
| `sensors/pipeline/alerts` | anomaly service | dashboard/ops | operator alerts |

---

## Query Plane with SAM

SAM is used as a downstream query consumer, not an ingestion processor.

### Practical interaction model

- **Default:** short status response
- **On request:** deeper analysis with additional tool/data retrieval

This keeps routine usage fast and low-cost while preserving depth when needed.

---

## Data Center HVAC Variant

The same pattern maps to HVAC telemetry:
- normalize BACnet/Modbus/MQTT into versioned MQTT contracts
- apply deadband/transition/dedupe before AI
- route event families for operations, compliance, and incident handling

Example versioned topics:
- `dc/v1/raw/{site}/{room}/{row}/{rack}/{asset}/{metric}`
- `dc/v1/event/{site}/{severity}/{eventType}`
- `dc/v1/sketch/{site}/{room}/{incidentId}`

---

## Results That Matter

- LLM calls dropped from "per event" to "per operator query."
- Event stream remained broker-centric and decoupled.
- Operators received clearer summaries with less alert fatigue.

Most importantly: the architecture stayed aligned with EDA principles while still delivering AI value.

---

## Trade-offs

- AI only knows what the sketch schema preserves.
- Sketch quality becomes a design-critical concern.
- This pattern supports operator decisioning, not sub-second autonomous control loops.

---

## Try the Demo

```bash
cd sam && source .venv/bin/activate
python src/demo_publisher.py &
python src/deadband_service.py &
python src/sketch_service.py &
python src/anomaly_service.py &
sam run
```

Then query in Web UI:
- "What's happening with m1?"
- "Analyze m2 critical events in detail"

---

## Stack

- **Event broker:** Solace PubSub+ Cloud
- **Pipeline:** Python microservices + Paho MQTT
- **Store:** SQLite
- **AI orchestration:** Solace Agent Mesh (SAM)
- **LLM endpoint:** any OpenAI-compatible or local model via LiteLLM

---

## Read Next

- **Part 1:** [BLOG_POST_EXECUTIVE.md](BLOG_POST_EXECUTIVE.md)
- **Topic contracts:** [DC_TOPIC_VERSIONING_README.md](DC_TOPIC_VERSIONING_README.md)
