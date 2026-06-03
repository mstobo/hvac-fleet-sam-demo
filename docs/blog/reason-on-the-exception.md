# Reason on the Exception: Event-Driven IIoT AI That Survives Production Token Bills

**Status:** Published in-repo for GitHub Pages  
**Canonical URL:** `https://mstobo.github.io/hvac-fleet-sam-demo/blog/reason-on-the-exception.html`  
**Repo:** [hvac-fleet-sam-demo](https://github.com/mstobo/hvac-fleet-sam-demo) · **Live demo:** [AWS dashboard](http://ec2-18-116-251-212.us-east-2.compute.amazonaws.com/)

---

## Executive summary

- **The issue:** Many IIoT AI pilots treat live sensor traffic like chat—every reading (and every retry) can trigger the model. Finance feels it when tag counts grow: you pay for “normal” at reasoning prices.
- **What we saw:** On a small lab dashboard (~3 pumps), that pattern produced [about $500 in token spend in one day](#real-pilot-meter) and 1,100+ analysis attempts—not a projection.
- **The fix:** Handle filtering, summaries, and alerts on the data path first; use AI only when an operator asks or the fleet hits a **correlated emergency** (not on every publish).
- **Proof in this demo:** One automated fleet incident report (summary, timeline, causes, actions, three machine charts) landed at [~5¢ per incident](#bring-it-home-one-report-two-price-tags) (116k tokens ≈ $0.04 on Azure gpt-5-mini)—same stack, different architectural boundary.
- **Takeaway for leadership:** Pilot success is not production economics. The lever is *where* AI runs, not which model you pick. Technical depth (pipeline, tool budgets, token measurements) follows below.

---

Most IIoT AI pilots fail on **economics**, not model quality. Generative models belong on **high-value exceptions** and operator questions—not on every IIoT publish on the hot path. We measured that shape in our open fleet demo, and saw the same pattern on a small lab setup ([about $500 in token spend in one day](#real-pilot-meter), three sensors).

![The Token Burn Problem: stream-to-model vs filter-first, AI on exceptions](../token-burn-device-friendly.png)

*Caption: Stream-to-model (left) vs filter, sketch, and rule-detect first, then AI on exceptions (right).*

### Real pilot meter

In a small industrial dashboard—**three pumps**, one bearing-temperature stream, event broker connected, simulator running—the design treated **live telemetry like chat input**. Each forwarded reading, and retries when tools failed, triggered another LLM pass. The UI stacked live events, AI analysis, and recommended actions; the analysis column accumulated hundreds of identical tool errors while the actions column still produced long prescriptive text.

**Roughly 1,500 telemetry events and 1,100+ analysis attempts in 24 hours.** The gateway meter showed **about $500 in token spend**—not a spreadsheet projection. Spend was capped when **LiteLLM gateway controls** cut off the runaway path (a circuit breaker, not architecture). The durable fix: **never wire IIoT throughput to the reasoning engine—keep AI off the hot path.**

The [hvac-fleet-sam-demo](https://github.com/mstobo/hvac-fleet-sam-demo) stack implements the right-hand path: deadband, sketches, and rules on the broker path; agents only on exceptions.

---

## The uncomfortable truth

When tag counts grow, finance asks what you are paying **per sensor per day**.

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

It works with a handful of tags. It breaks at scale.

**Correct pattern:** keep event movement and filtering on the **broker-centric data plane**; invoke AI as a **downstream consumer** of curated state—when operators ask, or when a **high-value exception** (for example fleet-critical) fires.

| Cheap (data plane) | Expensive (reasoning plane) |
|--------------------|-----------------------------|
| Suppress statistically insignificant change | Correlate patterns across assets |
| Apply thresholds and zones | Rank probable causes |
| Rolling window stats | Communicate in operator language |
| Deterministic alerts | Recommend actions under uncertainty |

---

## What we built (reference demo)

The [hvac-fleet-sam-demo](https://github.com/mstobo/hvac-fleet-sam-demo) repository is an open, runnable stack:

**Data plane:** nine cooling points (`machine-00x` × inlet · outlet · motor) → raw MQTT → deadband → sketch → anomaly → `sensor_data.db` / `chart_data.db` (no LLM per reading).

**Reasoning plane:** operator chat or `FLEET_CRITICAL` → `analysis-request` → SAM (SECTION A: 3× machine context + 3× machine charts, not 9× per-point).

See the [HTML architecture diagram](reason-on-the-exception.html#demo) on the published blog page.

**One-liner:** filter, sketch, and rule-detect on the broker path; agents reason only on exceptions.

Technical setup: [README](https://github.com/mstobo/hvac-fleet-sam-demo#readme) and [deploy/aws](https://github.com/mstobo/hvac-fleet-sam-demo/blob/main/deploy/aws/README.md).

---

## Sketches, SoT, and where chain-of-thought belongs

Two patterns, two planes—don’t mix stream-time compression with incident-time reasoning.

### Data plane: sketches (SoT-inspired)

A **sketch** is a deterministic one-liner per forwarded reading (Python templates, **no LLM on ingest**): zone, delta %, 30s stats, severity. [Sketch-of-Thought](https://arxiv.org/abs/2503.05179) compresses how models **write** reasoning; we compress what they **read** at ingress.

### Reasoning plane: chain-of-thought on the exception

[Chain-of-thought](https://arxiv.org/abs/2201.11903) fits hypotheses and ranked actions **once per incident**—curated tools, SECTION A budgets, sections 1–8—not on every publish.

| Plane | Pattern | LLM cost |
|-------|---------|----------|
| Data plane | Sketches (+ optional jargon) | **0** on ingest |
| Reasoning plane | Structured CoT | **1×** per exception |

### Why language—and NL vs jargon

Models reason over **language**, not raw sample arrays. Sketches land as short lines in SQLite so agents see narrative context, not floats.

**NL** sketches read like operator notes. **Jargon** (Expert-Lexicon) keeps the same facts in a compact lexicon—**smaller DB rows** and fewer **input tokens** when incident context loads (~70% less sketch text in our offline lab).

```text
NL:     machine-002:motor_temp_c recorded a 6.2% spike to 80.4°C. Zone: CRITICAL.
Jargon: m2:mot Δ↑6.2% T80.4 Z:C μ30=76.1[74-82] !CRIT
```

Dashboard **NL / Jargon** toggle or `SKETCH_STYLE=jargon` · [sketch-token-lab](https://github.com/mstobo/hvac-fleet-sam-demo/tree/main/tools/sketch-token-lab) · [production guide](../FLEET_ANALYSIS_PRODUCTION.md). The dashboard toggle calls chart-query `/admin/sketch-style` and applies to **new** sketches only (Chart API must be configured on the live demo).

---

## Case study: automated fleet analysis (SECTION A)

When a **correlated** fleet-critical condition is detected (default: ≥50% of active cooling assets in CRITICAL), the pipeline publishes one `analysis-request`. The fleet-analysis gateway routes to **FleetQueryAgent** with a **hard tool budget**—three machine-level incident contexts, three combined machine charts, no per-point forensic loop, one dispatch call. The report is **sections 1–8** once at fleet level with three `machine-plotly-html` URLs under Chart Evidence.

Orchestration without **budgets** invites the expensive pattern: nine assets × twenty-five sketches × multiple chart tools. Full SECTION A limits and env knobs: **[Fleet analysis production guide](../FLEET_ANALYSIS_PRODUCTION.md)**.

---

## Bring it home: one report, two price tags

The left side of the token-burn diagram is what the [~$500/day pilot](#real-pilot-meter) looked like in practice—telemetry treated like chat, over and over. The right side is what operators need when the fleet is in trouble: **one** *Automated Fleet Analysis* with ranked causes, actions, dispatch notes, three machine charts, and an honest token footer.

| | Stream → LLM (pilot) | Reason on the exception (this demo) |
|--|----------------------|-------------------------------------|
| **Spend** | **~$500 / day** | **~5¢ / incident** |
| **Scale** | ~3 sensors | Full fleet trigger (`FLEET_CRITICAL`) |
| **LLM calls** | 1,100+ analysis attempts in 24h | **One** batched report per correlated incident |
| **Mechanism** | Every forwarded reading → model | Filter → sketch → rule-detect; model on exception only |

Abbreviated excerpt from a production **Automated Fleet Analysis** Slack message (`analysis-response` → `analysis-to-slack`). Sections 3–8 shortened; chart URLs truncated.

```text
Automated Fleet Analysis

1) Summary
Fleet-wide thermal stress: 8 of 15 active cooling signals in CRITICAL across machines 001–003.
Correlated motor and outlet excursions; prioritize machine-002 motor bearing path.

2) Timeline
Escalation over ~12 minutes: machine-002 motor_temp_c leads, then outlet temps on 001 and 003.

Chart Evidence:
- machine-001: …/machine-plotly-html?asset_id=machine-001
- machine-002: …/machine-plotly-html?asset_id=machine-002
- machine-003: …/machine-plotly-html?asset_id=machine-003

… sections 3–8 (causes, actions, risk, dispatch) …

---
LLM usage (this run): 116,038 tokens (110,970 in / 5,068 out)
Estimated LLM cost (Azure gpt-5-mini): … ≈ $0.04 USD for this fleet analysis.
```

That footer is generated from gateway metadata ([`format_llm_usage_footer`](../../sam/src/fleet_analysis_response.py))—not a spreadsheet. Jun 2026 production run; replicate via [Bring your own Slack](../demo/SLACK_BYO.md).

---

## We measured it: what moved the token meter

More runs on the same SECTION A 3+3 tool shape—token counts always come from footers like the one above:

| Run | ~Total tokens | What differed |
|-----|----------------|---------------|
| Production FLEET_CRITICAL (Jun 2026) | 116,038 | 110,970 in / 5,068 out · sections 1–8 · 3× machine charts · ≈ **$0.04 USD** (Azure gpt-5-mini) |
| NL sketches | ~196k | 3× machine incident context + 3× machine Plotly |
| Jargon sketches | ~135k | Same tools; smaller sketch payloads in tool JSON |
| Heavy context | ~308k | Same tools; **large** incident bundles (~36k characters/machine)—input volume, not extra tools |

Dollar estimates use configurable per-1K input/output rates (`LLM_COST_*` env vars in the stack). At Azure gpt-5-mini pricing, one full fleet incident report at ~116k tokens is on the order of **four cents**—the contrast with stream→LLM pilot economics is architectural boundary, not model choice.

**Lessons:**

1. **Input dominates.** Good runs still show completion on the order of ~1–2k tokens; the Jun 2026 run was ~5k out.
2. **Jargon is a proven input lever** in this demo (~30% total reduction NL→jargon with same workflow).
3. **Output `max_tokens` caps** are a safety rail for runaway prose; they do not fix megabyte tool returns.
4. **Fleet sketch cap** trims the largest repeatable tool blob—see production guide for defaults and A/B order.

Details: **[Fleet analysis production guide](../FLEET_ANALYSIS_PRODUCTION.md)**.

---

## Production habits (summary)

1. **AI off the IIoT hot path** — filter, sketch, and rule-detect before any model sees telemetry.
2. **Tool budgets on automation** — SECTION A is the template; curiosity on the stream is what scales cost.
3. **Measure one change at a time** — report token footers on repeated FLEET_CRITICAL runs (Slack, logs, or BYO channel).

Checklist, env vars, SECTION A table, verification steps: **[Fleet analysis production guide](../FLEET_ANALYSIS_PRODUCTION.md)**.

---

## Try it

The **public live demo** runs the pipeline dashboard and **Fleet chat (SAM)** in the browser—no corporate Slack on the shared host. Automated fleet analysis can fan out to Slack in your workspace via [Bring your own Slack](../demo/SLACK_BYO.md).

- **[Live demo](http://ec2-18-116-251-212.us-east-2.compute.amazonaws.com/)** — pipeline dashboard, FLEET_CRITICAL preset, Fleet chat (SAM) tab  
- **[Fleet analysis production guide](../FLEET_ANALYSIS_PRODUCTION.md)** — deploy, tuning, verification  
- **[GitHub repo](https://github.com/mstobo/hvac-fleet-sam-demo)** — source and AWS Compose  
- **[Bring your own Slack](../demo/SLACK_BYO.md)** — your workspace, your channel  

**Video walkthrough:** *Coming soon* (~13 min, FLEET_CRITICAL on the live dashboard + automated analysis output; Slack shown via screenshot).

---

## About this article

Open reference implementation for event-driven industrial telemetry + Solace Agent Mesh. Numbers cited are from demo runs on the authors’ stack; reproduce on your environment with the same `analysis-request` path and compare report token footers.

*May 2026*
