# The Token Burn Problem: Why IIoT AI Projects Fail

**Most industrial AI initiatives do not fail because model quality is poor. They fail because architecture makes cost scale with telemetry volume.**

---

## The Pattern That Breaks at Scale

A common pilot pattern is:

`Sensor Event -> LLM -> Insight`

It looks great with a handful of sensors. Then production volume arrives, and the bill grows faster than the value. Most events are normal fluctuations, but each still gets charged as reasoning work.

At typical pricing assumptions, routing every message through an LLM can move from "interesting pilot cost" to "unfundable operating model" very quickly.

---

## The EDA Pattern That Scales

The fix is to keep high-throughput logic in the event system and reserve AI for operator questions.

### Data Plane (deterministic)
- ingest telemetry
- filter noise (deadband, thresholds, drift/rate checks)
- generate compact event sketches
- publish alerts and incident lifecycle events

### Query Plane (AI as optional subscriber)
- invoked only on demand by humans
- consumes pre-computed sketches and incident timelines
- returns short status by default, deep analysis on request

This is one EDA architecture with decoupled consumers. AI is not in the hot path.

---

## Why This Changes the Economics

When AI is called per event, cost scales with sensor count.  
When AI is called per question, cost scales with operator query volume.

That is the key move from a brittle architecture to a sustainable one.

| Approach | Cost Driver | Outcome |
|---|---|---|
| Every message -> AI | Telemetry volume | Explodes with scale |
| Query plane only | Human query volume | Predictable and controllable |

---

## Data Center HVAC Example

The same pattern applies cleanly to HVAC operations:
- normalize BACnet/Modbus/MQTT into versioned MQTT topics
- gate events before AI (deadband, state transitions, dedupe)
- fan out curated events to dashboard, alerts, CMMS, and SAM

Recommended event families:
- `TelemetryAccepted`
- `CoolingDriftDetected`
- `HumidityRiskDetected`
- `PressureContainmentRiskDetected`
- `IncidentOpened/Updated/Closed`
- `OperatorQueryRequested`

---

## Why It Helps Audience Reach

This framing is easier for broad technical audiences:
- Solace/EDA remains the core story
- AI is shown as a high-value consumer, not a mandatory processing step
- architecture principles transfer across industries, not only HVAC

---

## Bottom Line

1. Keep event refinement in the data plane.
2. Use AI in the query plane for operator-facing reasoning.
3. Design for fan-out and decoupled subscribers from day one.

You get lower cost, clearer operational behavior, and a system that scales without rewriting the architecture.

---

## Next Steps

- **Technical deep-dive:** [BLOG_POST.md](BLOG_POST.md)
- **Versioned topic contract:** [DC_TOPIC_VERSIONING_README.md](DC_TOPIC_VERSIONING_README.md)
- **Deployment notes:** [DEPLOYMENT.md](DEPLOYMENT.md)
- **Repository:** [README.md](README.md)

---

*Built with **Solace PubSub+** and **Solace Agent Mesh (SAM)**. The pattern remains vendor-neutral because AI stays a downstream query consumer.*
