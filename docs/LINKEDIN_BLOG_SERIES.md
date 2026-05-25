# LinkedIn Distribution Playbook (Micro-Posts → Pillars)

> **Publishing order:** See [`CONTENT_STRATEGY.md`](CONTENT_STRATEGY.md) — **blog first**, **demo video second**, then LinkedIn posts that **link to** those anchors. Do not treat this file as an eight-part LI article series.

**Working title:** *Reason on the Exception, Not the Stream*  
**Subtitle hook:** How we cut fleet AI runs from ~300k tokens to ~135k—without dumbing down the story operators see.

**Audience:** IIoT / facilities / reliability leaders, architects, and “pilot succeeded, production bill killed the project” crowd.  
**Tone:** Practitioner story (what we built, what we measured, what we’d do next)—not a product pitch.  
**Proof anchor:** Open demo + deterministic MQTT pipeline ([hvac-fleet-sam-demo](https://mstobo.github.io/hvac-fleet-sam-demo/)); numbers from real fleet-analysis runs (NL ~196k, jargon ~135k, pathological ~308k).

**LinkedIn format (after pillars exist):**
- **Short posts** (~400–900 characters) + **one link** (blog, video chapter, or repo).
- Optional **carousels** (5 slides) that end with “full story → [blog URL]”.
- **No** long LI-only articles duplicating the blog.

---

## Series arc (8 posts)

| # | Title | Job of the post |
|---|--------|-----------------|
| 1 | The token bill that kills IIoT AI pilots | Problem + economics (why “sensor → LLM” fails) |
| 2 | LLMs are reasoning engines, not stream processors | Mental model + data plane vs control plane |
| 3 | Sketches: compress the story before the model reads it | Deterministic NL/jargon summaries (zero LLM on hot path) |
| 4 | We measured it: 196k vs 135k vs 308k tokens | Honest A/B: what moved the needle (and what didn’t) |
| 5 | Tool budgets beat hope: 3 machines, 3 charts, no forensic loop | SECTION A pattern—agents need guardrails |
| 6 | Is replay cheaper? Only if you stop re-reasoning | Solace replay vs SQLite vs cached incident reports |
| 7 | Stale reports aren’t wrong—they’re snapshots | Event-sourced narrative for audit vs live ops |
| 8 | What we’d ship next (10-sketch cap, cached analysis, replay dedupe) | Practitioner roadmap + invite to discuss |

---

## Post 1 — The token bill that kills IIoT AI pilots

**Hook (first line):**  
Your IIoT AI pilot didn’t fail on accuracy. It failed when finance asked what you’re paying per sensor per day.

**Body beats:**
- 2s telemetry × thousands of points → billions of messages/year.
- Routing every reading through an LLM = paying reasoning rates to hear “normal” 80% of the time.
- Pilot math (10 sensors) hides production math (100–10,000 sensors).
- Tie to WHITEPAPER: $0.01/1K tokens × ~200 tokens/msg scales to absurd annual line items.

**Close / CTA question:**  
Have you seen a production AI budget survive first contact with real tag counts? What broke first—latency or cost?

**Hashtags (pick 3–5):** `#IIoT` `#IndustrialAI` `#EventDriven` `#DigitalTwin` `#Facilities`

**Carousel idea (5 slides):** Pilot → Production table | “Sensor → LLM” diagram (crossed out) | 70–80% noise stat | One-line fix teaser | “Post 2: the split architecture”

---

## Post 2 — LLMs are reasoning engines, not stream processors

**Hook:**  
If your architecture diagram has “LLM” on the same arrow as every MQTT publish, you’ve misclassified the workload.

**Body beats:**
- **Data plane (cheap):** filter, threshold, aggregate, template summaries, alert rules, persist rollups.
- **Control / reasoning plane (expensive):** synthesize across assets, rank causes, write exec-ready narrative, recommend dispatch—on **exceptions**.
- Broker-centric EDA: movement and filtering at the edge of the stream; AI as a **consumer** of curated state.
- One-liner from demo script: *filter, sketch, rule-detect on MQTT; SAM when operators ask or fleet goes critical.*

**Diagram (ASCII for carousel or article):**
```
Sensors → Deadband → Sketch → Rules/Fleet status → SQLite
                              ↓ (FLEET_CRITICAL only)
                         Analysis request → SAM → Report
```

**CTA question:**  
Where does your stack draw the line between “processing” and “reasoning”?

---

## Post 3 — Sketches: compress the story before the model reads it

**Hook:**  
We don’t ask GPT to watch 2-second temperature ticks. We give it **operator language** built in Python—at **zero** generative cost on the stream.

**Body beats:**
- **Sketch** = deterministic template: zone, Δ%, 30s window stats, CRITICAL/WARNING flags.
- Related idea: [Sketch-of-Thought](https://arxiv.org/abs/2503.05179) compresses *model reasoning*; we compress *telemetry narrative* on the data plane.
- **Jargon mode** (Expert-Lexicon style): same facts, ~70% fewer tokens when the agent reads them (`m2:mot Δ↑ … Z:C`).
- Sketches land in SQLite; agents **read** curated history—they don’t **generate** the firehose.

**Sample contrast (include in article):**
- NL: `machine-002:motor_temp_c recorded a 6.2% spike to 80.4°C… Zone: CRITICAL.`
- Jargon: `m2:mot Δ↑6.2% T80.4 Z:C μ30=76.1[74-82] !CRIT`

**CTA question:**  
Would your ops team trust template summaries, or do they insist on LLM prose for every event?

---

## Post 4 (article) — We measured it: 196k vs 135k vs 308k tokens

**Hook:**  
“Just add an agent” turned our fleet incident run into a six-figure-token conversation. Here’s what actually moved the meter.

**Sections:**

### What we were trying to do
Automated **FLEET_CRITICAL** analysis: one Slack-ready report, sections 1–8, three combined machine charts, mock dispatch—**without** nine per-point forensic tool loops.

### Three runs (same tool shape, different context cost)
| Run | ~Total tokens | What happened |
|-----|----------------|---------------|
| NL sketches | ~196k | 3× machine `get_incident_context` + 3× `get_machine_plotly_spec` |
| Jargon sketches | ~135k | Same tools; smaller sketch payloads in tool JSON |
| Heavy path | ~308k | Same 3+3 tools—but **huge** incident context (~36k chars/machine), not “wrong” extra tools |

**Lesson:** Input dominates. Output caps help on runaway prose; they don’t fix megabyte tool returns.

### Levers we ranked by expected return
1. **Fleet sketch cap** (25 → 10 on `machine-00x` scope)—trim largest tool blob; accuracy trade-off mostly on *timeline depth*, not “is it on fire?”
2. **Jargon sketches**—big input win, already proven in our runs
3. **Debug flags off**—stop the model pasting `section_7_lines`
4. **`max_tokens` on completion**—safety rail, not the main savings
5. **Prompt/tool budget**—forbidden `get_plotly_spec` in fleet SECTION A; exactly three `machine-plotly-html` URLs

### What we’d A/B next
Paste Run A (limit 25) vs Run B (limit 10) with Slack footers—same trigger, same `SKETCH_STYLE`—and diff sections 2–6, not just tokens.

**CTA question:**  
What’s the largest **tool return** you’ve seen accidentally stuffed into an agent context?

---

## Post 5 (article) — Tool budgets beat hope

**Hook:**  
Our agent *knows* how to investigate. Left unchecked, it investigates **nine assets × twenty-five sketches** and calls it thorough.

**Body beats:**
- **SECTION A** (automated fleet): hard budget—3× incident context (per **machine**, not per point), 3× combined Plotly, ≤1 dispatch call; **forbidden** per-point chart tools.
- **SECTION B** (everything else): six sentences, no forensic loop.
- Post-process: collapse duplicate sketch lines; validate report shape before Slack.
- Why orchestration without **budgets** reproduces the expensive pattern every time.

**Pull quote:**  
*Agents don’t need more freedom on hot paths—they need fewer tempting tools.*

**CTA question:**  
Do your production agents have **enforced** tool budgets, or only prompt pleading?

---

## Post 6 — Is replay cheaper? Only if you stop re-reasoning

**Hook:**  
“Replay from Solace” sounds like a token discount. It isn’t—unless you change **what** you replay.

**Body beats:**
- **Deterministic replay** (re-run deadband/sketch into DB): still **zero** LLM on the write path; SAM still reads SQLite the same way → **~same** tokens if you analyze again.
- **Replay `analysis-request`**: second SAM run = second bill unless you **dedupe** on `correlation_id`.
- **Replay cached report**: **zero** tokens on replay—you’re re-delivering a **snapshot**, not re-thinking.
- **Replay raw MQTT into the LLM**: usually **more** tokens, not fewer.

**One-line takeaway:**  
Replay saves money when it republishes an **artifact**, not when it re-asks the model the same question.

**CTA question:**  
Do you treat message replay as **state rebuild** or **incident redelivery**?

---

## Post 7 — Stale reports aren’t wrong—they’re snapshots

**Hook:**  
A stored fleet analysis report *will* be stale—for live ops. For audit and post-incident review, that’s the point.

**Body beats:**
- Three layers: **live telemetry** → **deterministic status now** → **analysis artifact frozen at trigger T**.
- Store: `correlation_id`, `incident_window`, `generated_at`, report text, chart URLs, context fingerprint (`sketch_limit`, style, minutes).
- Label Slack: *snapshot as of …* — not a real-time dashboard.
- **New** FLEET_CRITICAL → new id → new analysis. **Replay** old id → republish stored JSON, skip SAM.

**Contrast table:**

| Question | Use live tools | Use stored report |
|----------|----------------|-------------------|
| What’s happening **now**? | ✓ | ✗ misleading |
| What did we say **at trigger**? | ✗ reinvented | ✓ |
| Demo / training replay | optional | ✓ cheap |

**CTA question:**  
How do you separate **operational state** from **incident narrative** in your architecture today?

---

## Post 8 — Roadmap: what we’d ship next

**Hook:**  
We have a demo that proves the economics. Here’s the production-hardening checklist we’d actually prioritize.

**Numbered list (comment-bait):**
1. Fleet-only sketch cap (10) with measured A/B vs 25 on real SQLite
2. Idempotent analysis: store `analysis-response` by `correlation_id`; replay = republish
3. `max_tokens` on general model as completion guardrail
4. EC2 defaults: debug sketch evidence **off**, jargon toggle for token demos
5. Optional: compact `incident_bundle` on the MQTT trigger to skip tool round-trips on replay

**Close:**  
Link to GitHub Pages + repo. Invite DMs for architects comparing Solace + agent mesh patterns.

**CTA question:**  
Which item would your org adopt first—**context caps** or **cached incident reports**?

---

## Publishing cadence (after blog + video are live)

| Week | Post | Points to |
|------|------|-----------|
| Launch | “Story is live” | **Blog** |
| +1 | Token stat 196k→135k | Blog §6 |
| +2 | Sketches / zero LLM on stream | Blog §4 |
| +3 | Tool budgets | Blog §5 + video ?t= |
| +4 | Demo video drop | **Video** |
| +5 | Replay myth | Blog §7 |
| +6 | Snapshots vs live | Blog §7 |
| +7 | Try the repo / live demo | GitHub Pages |

**Repurpose:** Blog sections → carousels; video chapters → LI native clips.

---

## Assets to create (optional, high impact)

| Asset | Purpose |
|-------|---------|
| Before/after token footer screenshot (NL vs jargon) | Post 4 credibility |
| Architecture diagram (data plane vs SAM) | Posts 2, 5 |
| “SECTION A tool budget” one-pager | Post 5 carousel |
| 30s screen recording: dashboard FLEET_CRITICAL → Slack report | Post 1 or 8 CTA |
| Link in bio: GitHub Pages value story + live demo URL |

---

## Voice guardrails

- Say **“we measured”** / **“in our demo”**—not “customers typically see.”
- Call **~308k** a context-volume lesson, not a failure—same tools, heavy sketch payload.
- Credit **Sketch-of-Thought** as related research, not as what we shipped.
- Mention **Solace / event mesh** generically where replay matters; keep SAM as “agent on curated state.”
- Avoid claiming dollar savings without your org’s actual $/token—use token counts and ratios.

---

## First draft — Post 1 (ready to paste, ~1,050 chars)

Most IIoT AI pilots don’t die on accuracy.

They die when someone multiplies:  
(tags) × (2-second publishes) × (tokens per message) × ($/1K tokens).

In a pilot with 10 sensors, the LLM can “watch the stream” and feel brilliant.

At production scale, you’re paying **reasoning rates** for a answer you already knew: *normal, normal, normal, normal…*

The architectural mistake is subtle: treating an LLM like a **stream processor**.

It isn’t. It’s a **reasoning engine**.

The fix we’ve been demoing is boring on purpose:

→ Filter noise on the MQTT path (deadband)  
→ Summarize in **deterministic** operator language (“sketches”)—no generative AI on the hot path  
→ Rule-detect fleet-critical conditions  
→ Invoke the agent **only** on the exception—with **tool budgets**, not unlimited curiosity

Next post: the two-plane diagram and where the broker stops being a chat pipe.

**Question for you:** What killed your last pilot—latency, token cost, or trust in the answers?

---

## Series metadata (for LinkedIn “Featured”)

**Featured title:** Reason on the Exception  
**Description:** An 8-part practitioner series on IIoT AI economics—deterministic event pipelines, sketch compression, agent tool budgets, token measurements, and why message replay isn’t a token hack unless you cache the incident narrative.

**Link:** https://mstobo.github.io/hvac-fleet-sam-demo/
