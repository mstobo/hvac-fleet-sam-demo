# Reason on the Exception: Event-Driven IIoT AI That Survives Production Token Bills

**Status:** Draft v1 · published in-repo for GitHub Pages  
**Canonical URL (after push):** `https://mstobo.github.io/hvac-fleet-sam-demo/blog/reason-on-the-exception.html`  
**Repo:** [hvac-fleet-sam-demo](https://github.com/mstobo/hvac-fleet-sam-demo) · **Live demo:** [AWS dashboard](http://ec2-18-116-251-212.us-east-2.compute.amazonaws.com/)

---

## The uncomfortable truth

### A three-sensor demo that spent $500 in a day

We recently saw a familiar pattern play out on a small industrial dashboard—**three pumps**, one bearing-temperature stream, Solace PubSub+ connected, simulator running. Nothing enterprise-scale. Nothing “big data.”

The architecture treated **live telemetry like chat input**: every forwarded reading (and retries when tools failed) drove another trip through the LLM. The UI told the story in three columns—**live events**, **AI analysis**, **recommended actions**—and the middle column filled with hundreds of identical failures (“unexpected error during tool execution”) while the right column still grew long, prescriptive paragraphs (shift schedules, EUR downtime ranges, approval chains). **Roughly 1,500 telemetry events and 1,100+ analysis attempts in a single session window**—for three sensors.

**About $500 in token spend in one day.** Not a forecast from a whitepaper table—a real bill on a gateway meter before anyone scaled to a plant.

The bleed stopped when **LiteLLM gateway controls** throttled the runaway path. That is a safety net, not a design. The proper fix is upstream: **never wire IIoT throughput to the reasoning engine—keep AI off the hot path.**

That demo is the anti-pattern this article argues against. Our HVAC fleet reference stack exists to show the alternative: deterministic pipeline first, agent on the exception.

![The Token Burn Problem: stream-to-model vs filter-first, AI on exceptions](../token-burn-device-friendly.png)

*Caption: POC-friendly stream-to-model vs event-driven filtering and AI on exceptions—generative models stay off the IIoT hot path in the reference architecture (right).*

---

Many IIoT AI pilots do not fail on model quality. They fail when finance asks what you are paying **per sensor per day** after real tag counts show up.

A point publishing every two seconds generates on the order of **43,000 messages per sensor per day**. If each message becomes a few hundred tokens through an LLM, pilot economics (ten sensors) hide production economics (hundreds or thousands of sensors). You are paying **reasoning rates** to hear *normal, normal, normal* most of the time.

That is not a model problem. It is an **architecture** problem.

| Scale | Sensors | Messages/day (2s interval) | Illustrative annual AI cost* |
|-------|---------|---------------------------|------------------------------|
| Pilot | 10 | ~432K | ~$315K |
| Production | 100 | ~4.3M | ~$3.1M |
| Enterprise | 1,000 | ~43M | ~$31M |

\*Illustrative: ~200 tokens/message at $0.01/1K tokens if **every** reading goes through an LLM. Your pricing and tokenization will differ; the **scaling shape** does not.

---

## LLMs are reasoning engines, not stream processors

The natural pilot diagram is:

```text
Sensor → LLM → Insight
```

It works with a handful of tags. It breaks at scale because an LLM is built to **synthesize, hypothesize, and narrate**—not to filter 2% deadband noise on millions of events.

| Cheap (data plane) | Expensive (reasoning plane) |
|--------------------|-----------------------------|
| Suppress statistically insignificant change | Correlate patterns across assets |
| Apply thresholds and zones | Rank probable causes |
| Rolling window stats | Communicate in operator language |
| Deterministic alerts | Recommend actions under uncertainty |

**Correct pattern:** keep event movement and filtering on the **broker-centric data plane**; invoke AI as a **downstream consumer** of curated state—when operators ask, or when a **high-value exception** (for example fleet-critical) fires.

---

## What we built (reference demo)

The [hvac-fleet-sam-demo](https://github.com/mstobo/hvac-fleet-sam-demo) repository is an open, runnable stack:

```text
┌─────────────────────────────────────────────────────────────┐
│ DATA PLANE (no LLM per reading)                             │
│  MQTT raw → deadband → sketch → anomaly → SQLite + charts   │
└──────────────────────────────┬──────────────────────────────┘
                               │ summaries + alerts
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ REASONING PLANE (on demand)                                 │
│  Operator chat · FLEET_CRITICAL → analysis-request → SAM    │
│  Tools: incident context, machine Plotly, dispatch (mock)   │
└─────────────────────────────────────────────────────────────┘
```

**One-liner:** filter, sketch, and rule-detect on MQTT; let the agent reason only when operators ask—or when the fleet crosses a critical threshold.

Technical setup: [README](https://github.com/mstobo/hvac-fleet-sam-demo#readme) and [deploy/aws](https://github.com/mstobo/hvac-fleet-sam-demo/blob/main/deploy/aws/README.md).

---

## Sketches, SoT, and where chain-of-thought belongs

Industrial AI needs **two different compression tricks**, on **two different planes**. Conflating them is how a three-sensor demo spends $500 in a day.

### Sketch-of-Thought *on the stream* (data plane)

A **sketch** is a deterministic line (Python templates, **zero** generative cost on the hot path) attached to each forwarded reading: zone, delta %, 30s window stats, CRITICAL/WARNING flags.

**Before (raw stream to a model):**

```text
[43.2, 43.5, 65.8, 43.4, 43.1, 64.9, ...]
```

**After (sketch in SQLite / tool JSON):**

```text
machine-002:motor_temp_c recorded a 6.2% spike to 80.4°C. 30s window: mean 76.1°C, range [74–82°C]. Zone: CRITICAL.
```

[Sketch-of-Thought](https://arxiv.org/abs/2503.05179) (Aytes et al., 2025) shortens how a model **writes** its internal reasoning trace. Our **sketches** shorten what the model **reads** from telemetry—same *compress the story* instinct, but implemented as **deterministic ingress**, not SoT’s router or chunked symbolism on the LLM path. We do **not** run SoT prompting on every MQTT message.

### Chain-of-thought *on the exception* (reasoning plane)

[Chain-of-thought](https://arxiv.org/abs/2201.11903) prompting (Wei et al., 2022) is appropriate when you need hypotheses, timelines, and ranked actions—**once**, on a bounded incident—not on every 2-second publish.

In this demo, CoT shows up only when SAM runs:

1. **Curated inputs** — sketches, aligned chart URLs, alert rows from tools (`get_incident_context`, `get_machine_plotly_spec`), not live topic firehose.
2. **Mandated tool order** — incident context first, charts on the same UTC window, dispatch last (SECTION A enforces this with a hard budget).
3. **Structured output** — sections 1–8 (Summary → Timeline → Severity → Causes → Actions → Forensics → Data gaps → Dispatch) so reasoning becomes operator-visible prose instead of an unbounded internal monologue.
4. **Multi-turn tool loop** — the agent may take several LLM turns to call tools and assemble the report; that is CoT **orchestrated** by gateway + agent instructions, not CoT **attached** to each sensor reading.

| Plane | Pattern | What it compresses | LLM cost |
|-------|---------|-------------------|----------|
| Data plane | SoT-*inspired* sketches (optional jargon lexicon) | Telemetry → narrative **before** the agent | **0** on ingest |
| Reasoning plane | Structured CoT (tools + section template) | Curated state → incident report | **1× per exception** (chat or fleet-critical) |

The $500 anti-pattern is **chain-of-thought on the stream**: analysis column retries, verbose “recommended actions,” and token meters spinning on **normal** traffic. This architecture **front-loads SoT-style sketches** and **reserves CoT for fleet-critical and operator deep-dives**.

### Jargon mode (Expert-Lexicon style)

For agent context we also emit compact shorthand (~70% fewer tokens when the model reads them):

```text
m2:mot Δ↑6.2% T80.4 Z:C μ30=76.1[74-82] !CRIT
```

Toggle at runtime via dashboard **NL / Jargon** or `SKETCH_STYLE=jargon` (see [Fleet analysis production guide](../FLEET_ANALYSIS_PRODUCTION.md)).

Offline token comparison: [tools/sketch-token-lab](https://github.com/mstobo/hvac-fleet-sam-demo/tree/main/tools/sketch-token-lab).

---

## Case study: automated fleet analysis (SECTION A)

When a **correlated** fleet-critical condition is detected (default: ≥50% of active cooling assets in CRITICAL), the pipeline publishes one `analysis-request`. The fleet-analysis gateway routes to **FleetQueryAgent** with a **hard tool budget**—three machine-level incident contexts, three combined machine charts, no per-point forensic loop, one dispatch call. The report is **sections 1–8** once at fleet level with three `machine-plotly-html` URLs under Chart Evidence.

Orchestration without **budgets** invites the expensive pattern: nine assets × twenty-five sketches × multiple chart tools. Full SECTION A limits and env knobs: **[Fleet analysis production guide](../FLEET_ANALYSIS_PRODUCTION.md)**.

---

## We measured it: what moved the token meter

These are **real fleet-analysis runs** on the demo stack (same 3+3 tool shape; Slack footer metadata):

| Run | ~Total tokens | What differed |
|-----|----------------|---------------|
| NL sketches | ~196k | 3× machine incident context + 3× machine Plotly |
| Jargon sketches | ~135k | Same tools; smaller sketch payloads in tool JSON |
| Heavy context | ~308k | Same tools; **large** incident bundles (~36k characters/machine)—input volume, not extra tools |

**Lessons:**

1. **Input dominates.** Good runs still show completion on the order of ~1–2k tokens; the bill is prompt + multi-turn context.
2. **Jargon is a proven input lever** in this demo (~30% total reduction NL→jargon with same workflow).
3. **Output `max_tokens` caps** are a safety rail for runaway prose; they do not fix megabyte tool returns.
4. **Fleet sketch cap** trims the largest repeatable tool blob—see production guide for defaults and A/B order.

Details: **[Fleet analysis production guide](../FLEET_ANALYSIS_PRODUCTION.md)**.

---

## Replay, queues, and “stale” reports

**Solace replay** (or any broker replay) does **not** automatically reduce SAM tokens. Replay that re-runs the deterministic pipeline still ends with the agent reading SQLite the same way. Replay that fires `analysis-request` again runs the LLM again unless you **dedupe** on `correlation_id`.

**What does save tokens on replay:** store the finished **`analysis-response`** as an **incident snapshot** (report text, chart URLs, `generated_at`, window, context fingerprint) and **republish** it. That artifact is **point-in-time truth** for audit and training—not a live dashboard.

| Question | Live tools + new analysis | Stored snapshot |
|----------|---------------------------|-----------------|
| What is happening **now**? | ✓ | ✗ misleading |
| What did we say **at trigger**? | Reinvented | ✓ |
| Demo / compliance replay | Optional cost | ✓ cheap |

Label snapshots in Slack: *as of &lt;UTC&gt;, correlation id …* so operators do not confuse narrative with current telemetry.

---

## Production habits (summary)

1. **AI off the IIoT hot path** — filter, sketch, and rule-detect before any model sees telemetry.
2. **Tool budgets on automation** — SECTION A is the template; curiosity on the stream is what scales cost.
3. **Measure one change at a time** — Slack token footers on repeated FLEET_CRITICAL runs.

Checklist, env vars, SECTION A table, verification steps: **[Fleet analysis production guide](../FLEET_ANALYSIS_PRODUCTION.md)**.

---

## Try it

- **[Live demo](http://ec2-18-116-251-212.us-east-2.compute.amazonaws.com/)** — pipeline dashboard and FLEET_CRITICAL preset  
- **[Fleet analysis production guide](../FLEET_ANALYSIS_PRODUCTION.md)** — deploy, tuning, verification  
- **[GitHub repo](https://github.com/mstobo/hvac-fleet-sam-demo)** — source and AWS Compose  

**Video walkthrough:** *Coming soon* (~13 min, FLEET_CRITICAL → Slack report).

---

## About this draft

Open reference implementation for event-driven industrial telemetry + Solace Agent Mesh. Numbers cited are from demo runs on the authors’ stack; reproduce on your environment with the same `analysis-request` path and compare Slack LLM footers.

*Draft v1 — May 2026*
