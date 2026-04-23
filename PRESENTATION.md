# Intelligent IoT Without the AI Bill
## Separating Deterministic Compute from LLM Reasoning

---

# The Problem We're Solving

**Industrial IoT generates massive data volumes**

- Sensors publish every 2 seconds
- 100 sensors = 4.3 million messages/day
- Most readings are noise (tiny fluctuations)

**The tempting solution: "Just send it all to AI"**

```
Sensor → LLM → Insight
```

**The reality: $8,640/day for 100 sensors**

---

# Why "AI Everything" Fails at Scale

| Metric | Value |
|--------|-------|
| Messages/day (100 sensors) | 4,320,000 |
| Tokens per message | ~200 |
| Cost at $0.01/1K tokens | **$8,640/day** |
| Annual cost | **$3.1 million** |

**And 70-80% of those messages are noise.**

You're paying expert rates to repeatedly conclude "nothing interesting here."

---

# The Key Insight

## LLMs are reasoning engines, not data processors

| Data Processing | Reasoning |
|-----------------|-----------|
| Filter noise | Synthesize patterns |
| Apply thresholds | Generate hypotheses |
| Calculate statistics | Prioritize recommendations |
| Transform formats | Communicate naturally |

**Use the right tool for each job.**

---

# Our Architecture: Two Planes

```
┌─────────────────────────────────────────────────────────┐
│              DATA PLANE (Deterministic)                  │
│                   Zero LLM Cost                          │
│                                                          │
│   Sensors → Deadband → Sketch → Anomaly → Database       │
│              Filter    Generator  Detector               │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│             CONTROL PLANE (LLM-Powered)                  │
│               On-Demand Queries Only                     │
│                                                          │
│   "What's happening?" → LLM reads DB → Natural response  │
└─────────────────────────────────────────────────────────┘
```

---

# Stage 1: Deadband Filter

## Purpose: Eliminate noise

**The question:** "Has this reading changed enough to matter?"

```python
if delta_pct < 0.02 and not heartbeat_due:
    return "suppress"  # ~70% of readings
```

**Input:** Every sensor reading (thousands/second)
**Output:** Only meaningful changes (~30%)

**Cost:** Zero (Python code)

---

# How Deadband Works

```
Reading:    45.0  45.1  45.0  45.2  65.8  65.9  45.0
            ────  ────  ────  ────  ────  ────  ────
Action:     FWD   SUP   SUP   SUP   FWD   SUP   FWD
            (1st) (<2%) (<2%) (<2%) (38%) (<2%) (32%)
```

**Suppressed readings:** Still tracked in the rolling window for context
**Forwarded readings:** Continue through the pipeline

**Result:** 70% reduction in downstream processing

---

# Stage 2: Sketch Generator

## Purpose: Translate numbers to language

**The question:** "How do I describe this reading in plain English?"

```python
sketch = f"{sensor_id} recorded a {delta_pct:.1f}% spike to "
         f"{temp:.1f}°C. 30s window: mean {mean:.1f}°C, "
         f"range [{min:.1f}–{max:.1f}°C]. Zone: {zone}."
```

**Input:** Forwarded readings with window statistics
**Output:** Natural language "sketch"

**Cost:** Zero (string formatting, not LLM)

---

# Why Sketches Matter

## Without sketches (raw data to LLM):
```
[43.2, 43.5, 65.8, 43.4, 43.1, 64.9, 43.3, 66.2...]
```
LLM must: parse → baseline → calculate → interpret → respond

## With sketches (pre-computed summaries):
```
"sensor-001 recorded a 38% spike to 65.8°C. Zone: CRITICAL."
"sensor-002 recorded a 36% spike to 64.9°C. Zone: CRITICAL."
"sensor-003 recorded a 41% spike to 66.2°C. Zone: CRITICAL."
```
LLM can immediately reason: "Three simultaneous spikes — likely shared cause."

---

# Stage 3: Anomaly Detector

## Purpose: Generate alerts using rules

**The question:** "Does this reading warrant an alert?"

```python
if temperature >= 65.0:
    zone = "CRITICAL"
    severity = "HIGH"
    alert_type = "THRESHOLD_BREACH"
```

**Input:** Sketched readings
**Output:** Structured alerts with severity, type, description

**Cost:** Zero (threshold logic, not LLM)

---

# The Rolling Window

## Provides context for every decision

```
Time:        0s   2s   4s   6s   8s  10s  12s  ...  28s  30s
Temp:       45   45   46   45   65   66   64   ...  45   45
            ◄────────────── 30-second window ──────────────►
```

**Statistics calculated:**
- Mean: 51°C (baseline for comparison)
- Min/Max: 45-66°C (range of variation)
- Trend: RISING / FALLING / STABLE

**Why it matters:** A reading of 65°C means different things depending on whether the recent average was 64°C or 45°C.

---

# What Gets Written to the Database

| Table | Contents | Used For |
|-------|----------|----------|
| `sensor_readings` | Every forwarded reading | Historical analysis |
| `sketches` | NL summaries | "What's happening?" queries |
| `alerts` | Rule-based alerts | "Any problems?" queries |
| `fleet_status` | Aggregate health | "How's the fleet?" queries |

**The LLM never sees raw sensor data.**
**It reads pre-computed, human-readable summaries.**

---

# The Control Plane: Where LLM Shines

## User asks a question via chat:

> "What's been happening with the sensors?"

## LLM workflow:

1. **Tool selection:** "I should use `get_sketches()`"
2. **Query database:** Returns recent NL summaries
3. **Pattern recognition:** "Three sensors spiked simultaneously"
4. **Hypothesis generation:** "Likely a shared environmental cause"
5. **Recommendation:** "Check HVAC logs for 3:20-3:30 PM"

**This is what LLMs are good at — synthesis across pre-digested information.**

---

# Cost Comparison

| Approach | LLM Calls/Day | Cost/Day | Annual Cost |
|----------|---------------|----------|-------------|
| Every message → LLM | 4,320,000 | $8,640 | $3,150,000 |
| **Our pattern** | **~50** | **$0.50** | **$180** |

**Savings: 99.99%**

The difference: AI is invoked when humans ask questions (~50/day), not when sensors emit readings (~4.3M/day).

---

# Quality Comparison

## Traditional alerting:
```
ALERT: Sensor-001 exceeded 65°C
ALERT: Sensor-002 exceeded 65°C
ALERT: Sensor-003 exceeded 65°C
[...1,758 more alerts today...]
```
**Result:** Alert fatigue. Operators ignore everything.

## Our pattern:
```
"Correlated temperature spike across sensors 001-003 at 3:24 PM.
Near-simultaneous onset suggests shared environmental cause.

Recommended actions:
1. HIGH: Check HVAC logs for 3:20-3:30 PM
2. MEDIUM: Review power grid events
3. LOW: Adjust thresholds if transients recur"
```
**Result:** Operators understand what happened and what to do.

---

# When to Use Each Approach

## Deterministic Compute (Data Plane)
- High-volume data streams
- Well-defined rules (thresholds, filters)
- Latency-sensitive paths
- Cost-sensitive operations
- Auditable, repeatable logic

## LLM Reasoning (Control Plane)
- Low-frequency, high-value decisions
- Pattern synthesis across multiple sources
- Natural language interaction
- Hypothesis generation
- Nuanced recommendations

---

# The Architecture Principle

> **"If your LLM is seeing every event, your architecture is doing too little before it."**

The goal is not to make AI cheaper.
The goal is to make AI **necessary only where it adds value.**

---

# Microservices Implementation

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Deadband   │────►│    Sketch    │────►│   Anomaly    │
│   Service    │     │   Service    │     │   Service    │
└──────────────┘     └──────────────┘     └──────────────┘
       │                    │                    │
       │                    ▼                    ▼
       │              ┌──────────────────────────────┐
       │              │         SQLite DB            │
       │              │  sketches | alerts | fleet   │
       │              └──────────────────────────────┘
       ▼                           │
   Dashboard                       ▼
  (real-time)             ┌──────────────┐
                          │ Fleet Query  │
                          │    Agent     │◄──── User questions
                          │   (LLM)      │
                          └──────────────┘
```

---

# Inter-Service Communication

| Topic | Publisher | Subscriber |
|-------|-----------|------------|
| `sensors/temperature/#` | Sensors | Deadband Service |
| `sensors/pipeline/filtered` | Deadband | Sketch Service |
| `sensors/pipeline/sketched` | Sketch | Anomaly Service |
| `sensors/pipeline/alerts` | Anomaly | Dashboard |

**Each service is independent:**
- Can be scaled separately
- Can be updated without affecting others
- Can be monitored individually

---

# Key Takeaways

1. **Separate data processing from reasoning**
   - Processing: deterministic, cheap, scalable
   - Reasoning: expensive, valuable, on-demand

2. **Pre-compute natural language at the edge**
   - Sketches bridge raw data and AI understanding
   - Zero LLM cost for the translation

3. **LLM reads summaries, not streams**
   - Database accumulates pre-digested intelligence
   - AI queries when humans need insight

4. **Cost scales with users, not sensors**
   - 100 sensors or 10,000 — same AI cost
   - Unlocks IoT scale that was previously cost-prohibitive

---

# The Bottom Line

| Traditional | Our Pattern |
|-------------|-------------|
| AI processes every event | AI answers questions |
| $3.1M/year (100 sensors) | $180/year |
| Alert floods | Prioritized insights |
| Cost scales with data | Cost scales with users |
| Vendor lock-in | Modular, swappable |

**The technology exists. The architecture is proven.**

---

# Try It Yourself

```bash
# Start the data plane
python deadband_service.py &
python sketch_service.py &
python anomaly_service.py &

# Or combined:
python mock_pipeline.py &

# Start the control plane
sam run

# Ask questions at http://localhost:8000
"What's been happening with the sensors?"
```

---

# Questions?

**Resources:**
- Technical blog: `BLOG_POST.md`
- Executive summary: `BLOG_POST_EXECUTIVE.md`
- Architecture: `README.md`

**Built with:**
- Solace PubSub+ (event streaming)
- Solace Agent Mesh (AI orchestration)
- SQLite (demo) / TimescaleDB (production)
