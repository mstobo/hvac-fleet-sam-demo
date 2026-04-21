# Reducing MQTT Noise for LLM-Powered IoT Analytics

**How we built an event-driven architecture that processes thousands of sensor readings without bankrupting our AI budget**

---

> 📖 **This is Part 2 of a two-part series.**
> - **Part 1 (Business Case):** [The AI-Powered IoT Trap — Why Most Enterprises Are Paying Millions for Noise](BLOG_POST_EXECUTIVE.md) — economics, strategic implications, and leadership considerations
> - **Part 2 (You are here):** Technical implementation — architecture, code, and lessons learned

---

## Technical Thesis

**Move AI out of the event ingestion path and into the query/decision path — and replace raw telemetry with deterministic semantic summaries.**

This single architectural shift transforms two things simultaneously:

- **System behavior** — LLM latency is removed from the ingestion path, eliminating backpressure risk
- **Cost structure** — AI spend scales with user queries, not sensor event volume

Everything that follows is an implementation of this principle.

---

## The Problem: IoT Data Firehose Meets Expensive AI

We had a common challenge: a fleet of temperature sensors publishing readings every 2 seconds via MQTT. That's **1,800 messages per sensor per hour**. With just 3 sensors, we're looking at 5,400 messages/hour. Scale to 100 sensors? **180,000 messages/hour**.

Our goal was to use an LLM to provide intelligent fleet monitoring—detecting anomalies, correlating patterns across sensors, and generating actionable recommendations. The naive approach would be:

```
MQTT Message → LLM → Response
```

**The math doesn't work.** At $0.01 per 1K tokens, processing 180K messages/hour would cost ~$43/hour just for the input tokens. That's over **$1,000/day** for a simple monitoring system with just 3 sensors. Scale to 100 sensors and that figure climbs to **$8,640/day — over $3.1M/year** (see the [business case breakdown](BLOG_POST_EXECUTIVE.md) for the full cost analysis at scale).

## The Wrong Architecture: Event Mesh Gateway → LLM

Our first attempt used Solace Agent Mesh's Event Mesh Gateway to trigger LLM calls directly from MQTT events:

```
MQTT Event → Event Mesh Gateway → Generate Prompt → LLM → Response
```

This architecture has a fundamental flaw: **every sensor reading triggers an LLM call**, regardless of whether the data is interesting. Most IoT data is noise—tiny fluctuations that don't require AI analysis.

The consequences compound at scale:

- **LLM latency enters the ingestion path** — any slowdown or throttling in the LLM creates backpressure across the entire sensor pipeline
- **Backpressure risk grows with sensor count** — what's survivable at 3 sensors becomes a reliability problem at 100+
- **Most calls process non-events** — the LLM repeatedly concludes "everything is normal," at full token cost

This is not a model problem. It is a **system design problem** — and a better model won't fix it.

## The Right Architecture: Deterministic Pipeline + LLM Query Layer

We restructured into two distinct planes:

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
│   ┌─────────────────────────────────────────────────────┐      │
│   │                    SQLite                            │      │
│   │   • sensor_readings  • sketches  • alerts           │      │
│   └─────────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                   CONTROL PLANE (LLM)                           │
│              On-demand queries, natural language                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   User: "What's been happening with the sensors?"              │
│        │                                                        │
│        ▼                                                        │
│   ┌──────────────┐         ┌─────────────────────────┐         │
│   │  SAM Agent   │────────▶│  Tool: get_sketches()   │         │
│   │  (with LLM)  │         │  → SELECT * FROM sketch │         │
│   └──────────────┘         └─────────────────────────┘         │
│        │                                                        │
│        ▼                                                        │
│   "Correlated spike across all 3 sensors at 15:24 UTC.         │
│    Pattern suggests shared cause—check HVAC/power logs."       │
└─────────────────────────────────────────────────────────────────┘
```

### Key Components

**1. Deadband Filter (No LLM)**
Suppresses readings that haven't changed significantly from the last forwarded value:
```python
if delta_pct < 0.02 and not heartbeat_due:  # 2% threshold
    return "suppress"  # Don't process further
```
Result: **~70% of messages filtered out** before any further processing.

**2. Sketch Generator (No LLM)**
Creates natural language summaries for each significant reading:
```python
sketch = f"{sensor_id} recorded a {delta_pct:.1f}% spike to {temp:.1f}°C. "
sketch += f"30s window: mean {mean:.1f}°C, range [{min:.1f}–{max:.1f}°C]. "
sketch += f"Zone: {zone}."
if zone == "CRITICAL":
    sketch += " ⚠️ ANOMALY — immediate review required."
```
These sketches are **pre-computed** and stored. The LLM doesn't generate them—it reads them.

**3. Rule-Based Anomaly Detection (No LLM)**
Simple threshold logic for alerts:
```python
if temperature >= 65.0:
    zone = "CRITICAL"
    insert_alert(sensor_id, "SPIKE", "HIGH", description)
elif temperature >= 58.0:
    zone = "WARNING"
```

**4. SAM Query Agent (With LLM)**
The LLM is only invoked when a human asks a question. It uses tools to query the pre-processed data:

```yaml
tools:
  - get_sketches      # "What's been happening?"
  - get_recent_alerts # "Any critical alerts?"
  - get_fleet_status  # "How's the fleet?"
  - get_sensor_details # "Tell me about sensor-001"
```

## The Magic: Sketches Enable Pattern Recognition

The key insight is that **sketches are the bridge** between high-volume IoT data and LLM reasoning.

Without sketches, the LLM would need to:
1. Receive raw numbers: `[43.2, 43.5, 43.1, 65.8, 43.4, ...]`
2. Compute statistics
3. Detect patterns
4. Generate natural language

With sketches, the LLM receives:
```
"sensor-001 recorded a 38.4% spike to 65.8°C. Zone: CRITICAL."
"sensor-002 recorded a 37.1% spike to 64.2°C. Zone: CRITICAL."
"sensor-003 recorded a 39.2% spike to 66.1°C. Zone: CRITICAL."
```

Now the LLM can **reason across pre-digested summaries**:

> "Strongly correlated, near-simultaneous SPIKE/CRITICAL events across all three sensors. Pattern suggests a common cause (shared environmental event) rather than independent sensor drift."

This is what LLMs are good at—synthesis, hypothesis generation, and natural language output. Not number crunching.

> **📌 Pattern Note:** What we're calling "sketches" maps directly onto the **Sketch-of-Thought (SoT)** pattern in the AI literature — where raw data is compressed into a plain-language intermediate representation before being passed to the primary LLM. The key difference in our implementation: the sketch generator is **deterministic Python code**, not a secondary LLM. For structured sensor telemetry, this eliminates narrator-LLM cost entirely while producing more consistent, auditable output than a model-generated sketch would. Every sketch is stored in SQLite — making it inspectable, replayable, and explainable by design.

## Results

### System Behavior at Scale

| Dimension | Behavior |
|-----------|----------|
| Sensor volume | Scales linearly in the data plane — no LLM involvement |
| LLM usage | Scales with user query volume, not event volume |
| Cost | Fully decoupled from ingestion rate |
| Ingestion reliability | No LLM latency or backpressure in the data path |

### Cost Comparison

| Approach | Messages/Hour | LLM Calls/Hour | Est. Cost/Day |
|----------|---------------|----------------|---------------|
| Every message → LLM | 180,000 | 180,000 | $1,000+ |
| **Our approach** | 180,000 | **~10** (user queries) | **$0.01** |

### Quality Comparison

**Before (alert-only response):**
```
Total alerts: 1,758
CRITICAL: 725
WARNING: 1,033
```

**After (sketch-powered response):**
```
"Strongly correlated spike cluster at 15:24-15:27 UTC across all sensors.
Peak: sensor-002 dropped 21°C in 12 seconds.
Hypothesis: Shared cause—check HVAC/power logs.
Recommended: Investigate within 1 hour."
```

## Lessons Learned

### 1. Never Put LLMs in the Ingestion Path
This is the foundational architectural principle — everything else follows from it. The moment an LLM becomes part of your event ingestion flow, you've coupled your pipeline's reliability and cost to the latency and availability of an external AI service. For high-volume IoT, that coupling is fatal at scale. Deterministic code belongs in the ingestion path. LLMs belong in the query path.

### 2. LLMs Require Explicit Tooling Contracts
Our first version had the LLM calling `get_alerts` even for "what's happening" questions. We had to explicitly guide tool selection:

```yaml
instruction: |
  TOOL SELECTION GUIDE:
  
  "What's been happening?" / "Recent activity?"
  → USE get_sketches FIRST - these are pre-written summaries
  
  "Any alerts?" / "Critical events?"
  → USE get_recent_alerts
```

### 3. Separate Data Plane from Control Plane
Event-driven gateways that trigger LLM calls are useful for **low-volume, high-value events** (like Jira tickets). For high-volume IoT, you need a deterministic pipeline that doesn't involve the LLM.

### 4. Pre-compute Natural Language
The sketch generator does the "translation" from numbers to words **at ingestion time**, not query time. This means:
- Consistent formatting across all events
- No LLM cost for the translation
- LLM can focus on synthesis, not description

### 5. Sketches Are Auditable by Design
Because sketches are plain language stored in SQLite — not ephemeral LLM reasoning — you can always inspect exactly what the LLM saw before it gave an answer. Query `SELECT * FROM sketches WHERE sensor_id = 'sensor-001'` and you have a complete, human-readable record of every pre-processed event the AI could have drawn on. This is critical for regulated environments where AI-generated recommendations must be explainable and defensible. The sketch is your audit trail.

## Trade-offs and Limitations

No architecture pattern is universally applicable. This one has real constraints worth understanding before you adopt it:

| Limitation | Detail |
|------------|--------|
| **Loss of raw-data fidelity at query time** | The LLM can only reason about what was captured in the sketch. If your summary format omits a dimension (e.g., vibration), the AI cannot infer it — even if the raw reading was significant. |
| **Summary design is load-bearing** | A poorly structured sketch format limits AI reasoning quality. The schema you choose at implementation time becomes a long-lived contract. Get it wrong and the AI's answers will be consistently shallow. |
| **Not suitable for ultra-low-latency control loops** | This pattern is designed for operator-query workloads, not real-time closed-loop control. If you need sub-second automated response, deterministic rule systems remain the right tool — AI is advisory here, not actuation. |
| **Summary format versioning required** | As your monitoring requirements evolve, sketch schemas will need to change. Without a versioning strategy, historical sketches and new sketches become incompatible, breaking query continuity. Plan for this from day one. |

These are manageable constraints — not reasons to avoid the pattern — but they should inform your implementation design decisions.

## Architecture Summary

```
┌─────────────────────────────────────────────────────────┐
│  MQTT (1000s/sec) → Deterministic Pipeline → SQLite    │
│                            (no LLM cost)               │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────┐
│  User Question → LLM → Tools → SQLite → LLM → Answer   │
│                     (~10 calls/day)                    │
└─────────────────────────────────────────────────────────┘
```

**The LLM doesn't process events. It answers questions about pre-processed data.**

---

## Tech Stack

- **Event Broker**: Solace PubSub+ Cloud
- **Pipeline**: Python + Paho MQTT
- **Database**: SQLite (demo) / TimescaleDB (production)
- **AI Framework**: Solace Agent Mesh (SAM)
- **LLM**: Azure OpenAI via LiteLLM

## Try It Yourself

The full source code is available in this repository. To run:

```bash
# Start the data plane
cd sam && source .venv/bin/activate
python src/demo_publisher.py &   # Simulates sensors
python src/mock_pipeline.py &    # Processes → SQLite

# Start the control plane
sam run                          # SAM agents + WebUI

# Open http://localhost:8000 and ask:
# "What's been happening with the sensors lately?"
```

---

---

> *If your LLM is seeing every event, your architecture is doing too little before it. The goal is not to make AI cheaper. It is to make AI **necessary only where it adds value**.*

---

## Read Next

**Missed Part 1?** [The AI-Powered IoT Trap: Why Most Enterprises Are Paying Millions for Noise](BLOG_POST_EXECUTIVE.md) covers the business case, cost economics at scale (100–10,000 sensors), strategic implications for leadership, and implementation roadmap — no code required.
