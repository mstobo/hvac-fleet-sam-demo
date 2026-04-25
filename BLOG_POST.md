# Why Your IIoT AI Project Will Fail (And How to Fix It)

**The architecture mistake burning through enterprise AI budgets — and the pattern that cuts costs by 99%**

---

> 📖 **This is Part 2 of a two-part series.**
> - **Part 1 (Business Case):** [The Token Burn Problem](BLOG_POST_EXECUTIVE.md) — economics, strategic implications, and leadership considerations
> - **Part 2 (You are here):** Technical implementation — architecture, code, and lessons learned

---

## The Pattern That Kills IIoT AI Projects

Many IIoT AI projects fail within months of deployment. Not because the AI doesn't work — it works fine. They fail because the costs spiral out of control the moment real sensor data starts flowing.

The pattern is always the same:

1. Team builds a promising proof-of-concept with a few sensors
2. POC shows impressive AI-generated insights
3. Project scales to production sensor counts
4. Finance calls an emergency meeting about the AI bill
5. Project gets shelved or lobotomized

The root cause is architectural, and it's almost always this:

```
Sensor Event → LLM → Response
```

Every reading, every fluctuation, every unremarkable data point — sent to an LLM for analysis. The AI dutifully processes each one, mostly concluding "nothing interesting here," at full token cost.

**The math is brutal.** A single sensor publishing every 2 seconds generates 43,200 messages per day. At $0.01 per 1K tokens, that's roughly $8.60/day per sensor — just for input tokens. Scale to 100 sensors and you're burning **$860/day**. A thousand sensors? **$8,600/day — over $3.1M/year**.

For what? A system that's 70-80% dedicated to confirming normalcy.

---

## The Fix: Stop Using AI as a Data Processor

LLMs are reasoning engines, not data processors. When you route every sensor event through an LLM, you're using a specialist to do clerical work — at specialist prices, millions of times per day.

The architecture that works:

```
┌─────────────────────────────────────────────────────────────────┐
│                    DATA PLANE (No LLM)                          │
│              High-throughput, deterministic processing          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   MQTT Publisher (1000s msg/sec)                               │
│        │                                                        │
│        ▼                                                        │
│   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐       │
│   │   Deadband   │──▶│    Sketch    │──▶│   Anomaly    │       │
│   │    Filter    │   │  Generator   │   │  Detector    │       │
│   │  (suppress   │   │  (NL summary │   │  (rule-based │       │
│   │    noise)    │   │   per event) │   │   alerts)    │       │
│   └──────────────┘   └──────────────┘   └──────────────┘       │
│         │                   │                  │                │
│         ▼                   ▼                  ▼                │
│                         SQLite                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                   CONTROL PLANE (LLM)                           │
│              On-demand queries, natural language                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   User: "What's happening with the sensors?"                   │
│        │                                                        │
│        ▼                                                        │
│   LLM reads pre-computed sketches → Synthesizes → Responds     │
└─────────────────────────────────────────────────────────────────┘
```

**The LLM doesn't process events. It answers questions about pre-processed data.**

---

## The Implementation: Three Microservices, Zero LLM Tokens

The data plane is three independent Python services communicating via MQTT:

### 1. Deadband Service (`deadband_service.py`)

Filters noise before it propagates:

```python
if delta_pct < 0.02 and not heartbeat_due:  # 2% threshold
    return "suppress"
```

Result: **~70% of messages filtered** before any further processing.

Also maintains a rolling 30-second window for context:

```python
window = {
    "mean": 48.3,
    "min": 47.1,
    "max": 52.8,
    "trend": "STABLE"
}
```

### 2. Sketch Service (`sketch_service.py`)

Converts filtered readings into natural language summaries:

```python
sketch = f"{sensor_id} recorded a {delta_pct:.1f}% spike to {temp:.1f}°C. "
sketch += f"30s window: mean {mean:.1f}°C, range [{min:.1f}–{max:.1f}°C]. "
sketch += f"Zone: {zone}."
```

Output stored in SQLite:
```
"m1-temp-motor recorded a 30.7% spike to 77.1°C. Zone: CRITICAL. ⚠️ ANOMALY"
```

**This is the key insight:** The sketch is computed once at ingestion time, not at query time. The LLM reads pre-written summaries — it doesn't generate them.

### 3. Anomaly Service (`anomaly_service.py`)

Rule-based detection and fleet status tracking:

```python
if temperature >= 65.0:
    zone = "CRITICAL"
    generate_alert(sensor_id, "SPIKE", "HIGH")
```

Also tracks fleet-wide status for multi-sensor correlation.

### Inter-Service Communication (MQTT Topics)

| Topic | Publisher | Subscriber | Purpose |
|-------|-----------|------------|---------|
| `sensors/temperature/#` | Sensors | deadband_service | Raw readings |
| `sensors/pipeline/filtered` | deadband_service | sketch_service | Passed deadband |
| `sensors/pipeline/sketched` | sketch_service | anomaly_service | With NL summaries |
| `sensors/pipeline/alerts` | anomaly_service | Dashboard | Generated alerts |

### Data Center HVAC Variant (BACnet/Modbus/MQTT)

For data centers, apply the same architecture to cooling and environmental telemetry (temperature, humidity, differential pressure), with BACnet and Modbus points normalized into MQTT topics.

Use broker-level event gating before AI:
- deadband filtering for minor oscillations
- rate-of-change detection for fast thermal/pressure drift
- state transition events (`NORMAL -> WARNING -> CRITICAL`)
- dedupe/cooldown to prevent duplicate incident floods
- multi-signal correlation across room/row/rack windows

Example topic model:
- `dc/raw/{site}/{room}/{row}/{rack}/{asset}/{metric}`
- `dc/event/{site}/{severity}/{eventType}`
- `dc/sketch/{site}/{room}/{incidentId}`

Recommended routed event types:
- `CoolingDriftDetected`
- `HumidityRiskDetected`
- `PressureContainmentRiskDetected`
- `MultiSignalHotspotDetected`
- `IncidentOpened`
- `IncidentUpdated`
- `IncidentClosed`

The result is the same economic outcome: deterministic services absorb high-volume telemetry, while the LLM is invoked only for operator queries against pre-computed sketches and incident timelines.

---

## The Query Layer: Efficient by Default, Thorough on Demand

The LLM is invoked only when a human asks a question. But even here, we learned efficiency matters.

### The Problem We Discovered

Our first version produced thorough responses to every question — executive summaries, per-sensor breakdowns, root cause analysis, prioritized recommendations. Impressive, but wasteful for simple status checks.

A question like "What's happening with m1?" triggered:
- 6+ tool calls
- Multiple agent round-trips
- Artifact generation
- Multi-paragraph forensic reports

### The Fix: Tiered Response Depth

We tuned the agent to match response depth to question intent:

**Simple query** → Brief answer
```
User: "What's happening with m1?"
Agent: get_sketches(sensor_filter="m1") → 2 sentences
```

Output:
```
m1 is operating normally: inlet ~38°C, motor ~52°C, outlet ~47°C. 
All sensors in NORMAL zone with stable trends.
```

**Detailed analysis request** → Thorough investigation
```
User: "Analyze the m2 critical events in detail"
Agent: get_sensor_details() + get_recent_alerts() → Full report
```

Output: Executive summary, timeline analysis, cross-machine correlation, ranked root causes, prioritized recommendations.

**The user controls the depth by how they phrase the question.**

### Agent Instructions That Actually Work

```yaml
instruction: |
  ⚠️ EFFICIENCY MODE:
  - Give SHORT answers (2-4 sentences) unless user asks for detail
  - ONE tool call per question
  - NO artifacts for simple status questions
  
  TOOL SELECTION (pick ONE):
  | Question | Tool | Response |
  |----------|------|----------|
  | "What's happening with m1?" | get_sketches | Brief summary |
  | "Analyze in detail" | Multiple tools | Full report |
```

---

## The Results

### Cost Comparison

| Approach | Messages/Hour | LLM Calls/Hour | Est. Cost/Day |
|----------|---------------|----------------|---------------|
| Every message → LLM | 180,000 | 180,000 | $1,000+ |
| **This architecture** | 180,000 | **~10** (queries) | **$0.01** |

### Quality Comparison

**Before (LLM on every event):**
```
Total alerts processed: 180,000
"Everything normal" responses: 178,000 (99%)
Actual insights: 2,000
Cost: $1,000/day
```

**After (query layer only):**
```
User: "What's happening with m1, m2, and m3?"

Response:
- m1: Normal and stable — inlet ~38°C, motor ~52°C, outlet ~47°C
- m2: Multiple critical events — motor peaked ~75°C (CRITICAL); warrants review
- m3: Motor spiked to ~77°C earlier, now in WARNING zone (~58-60°C)

Tool calls: 1 (get_sketches)
Cost: ~$0.001
```

---

## Why Sketches Are the Key

The sketch pattern (sometimes called "Sketch-of-Thought" in AI literature) is what makes this work:

**Without sketches**, the LLM must:
1. Parse raw numbers: `[43.2, 43.5, 43.1, 65.8, 43.4, ...]`
2. Compute baselines and deviations
3. Assess statistical significance
4. Translate to natural language
5. Then finally reason about patterns

**With sketches**, the LLM receives:
```
"m1-temp-motor recorded a 30.7% spike to 77.1°C. Zone: CRITICAL."
"m2-temp-motor recorded a 38.4% spike to 75.2°C. Zone: CRITICAL."
```

Now it can immediately reason: *"Near-simultaneous critical events on m1 and m2 motors suggest a shared cause — cooling failure or power transient — not independent sensor drift."*

The sketch is computed once by deterministic Python code. The LLM reads it many times. That's the economics that work.

---

## Lessons Learned

### 1. Keep LLMs Out of the Ingestion Path

For high-throughput telemetry, this is the foundational rule. The moment an LLM becomes part of your event flow, you've coupled reliability and cost to an external AI service. At thousands of messages per second, that coupling kills projects.

**Event-triggered AI still makes sense for:**
- Low-frequency, high-value events (support tickets, approvals)
- Workflow automation (reports, summaries, routing)
- Document generation

The distinction is **volume, not capability**.

### 2. Pre-compute Natural Language at Ingestion

The sketch generator does translation from numbers to words **once, at ingestion time**. Benefits:
- Consistent formatting
- Zero LLM cost for translation
- LLM focuses on synthesis, not description

### 3. Tune Agent Responses for Efficiency

Default to brief. Let users explicitly request depth. This cut our average query cost by 80% while maintaining quality when it matters.

### 4. Sketches Are Your Audit Trail

Because sketches are plain language stored in SQLite, you can always inspect exactly what the LLM saw. Query `SELECT * FROM sketches WHERE sensor_id = 'm1-temp-motor'` and you have a complete record. Critical for regulated environments.

---

## Trade-offs

| Limitation | Detail |
|------------|--------|
| Loss of raw-data fidelity | The LLM only sees what's in the sketch. Design your sketch format carefully. |
| Summary design is load-bearing | A poorly structured sketch limits AI reasoning. Get the schema right upfront. |
| Not for real-time control loops | This is for operator queries, not sub-second automated responses. |

---

## Try It

```bash
# Start the data plane microservices
cd sam && source .venv/bin/activate
python src/demo_publisher.py &      # Simulates 9 sensors (3 machines × 3 sensors)
python src/deadband_service.py &    # Filters noise
python src/sketch_service.py &      # Generates NL summaries
python src/anomaly_service.py &     # Rule-based alerts

# Start the query layer
sam run

# Open http://localhost:8000 and ask:
# "What's happening with m1?" → Brief status (1 tool call)
# "Analyze m2 critical events in detail" → Full forensic report
```

---

## Tech Stack

- **Event Broker**: Solace PubSub+ Cloud
- **Pipeline**: Python microservices + Paho MQTT
- **Database**: SQLite
- **AI Framework**: Solace Agent Mesh (SAM)
- **LLM**: Azure OpenAI via LiteLLM

---

> *If your LLM is seeing every event, your architecture is doing too little before it. The goal is not cheaper AI. It's AI only where it adds value.*

---

## Read Next

**Missed Part 1?** [The Token Burn Problem](BLOG_POST_EXECUTIVE.md) covers the business case, cost economics at scale, and strategic implications — no code required.

**Need implementation contracts?** [DC_TOPIC_VERSIONING_README.md](DC_TOPIC_VERSIONING_README.md) defines versioned MQTT topics, event types, and schema evolution policy for data center HVAC streams.
