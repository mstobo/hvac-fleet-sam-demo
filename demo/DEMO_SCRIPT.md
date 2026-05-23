# Demo script — HVAC fleet: event-driven pipeline + SAM

**Audience:** Mixed technical / business (adjust depth per act).  
**Core message:** High-volume MQTT is processed **deterministically on the broker path**; LLMs run only on **curated state** and **high-value events**.  
**Duration:** 15–20 minutes live (+ Q&A). Deck-only: 8–10 minutes.

| Resource | URL / path |
|----------|------------|
| **Live demo (AWS)** | http://ec2-18-116-251-212.us-east-2.compute.amazonaws.com/ |
| **Value story (Pages)** | https://mstobo.github.io/hvac-fleet-sam-demo/ |
| **Technical setup** | [README.md](../README.md) · [deploy/aws/README.md](../deploy/aws/README.md) |
| **Architecture deck** | [ARCHITECTURE_DECK.html](ARCHITECTURE_DECK.html) |
| **Executive deck** | [EXECUTIVE_DECK.html](EXECUTIVE_DECK.html) |

**One-liner (memorize):**  
“We don’t stream every reading to an LLM. We **filter, sketch, and rule-detect** on MQTT, persist rollups in SQLite, and let SAM reason only when operators ask—or when the fleet crosses a critical threshold.”

---

## Story spine (maps to GitHub Pages value framework)

Use this as your thread; each act should land one beat.

| Act | Value beat | What you prove |
|-----|------------|----------------|
| 1 | **Protect spend** | Raw volume vs what actually moves downstream |
| 2 | **Signal over noise** | Deadband suppresses; filtered/sketched topics pulse |
| 3 | **Sketch, don’t spell out** | Template NL summaries—not generative AI on the hot path |
| 4 | **Rules before models** | Zones + fleet-critical fraction → alerts without LLM |
| 5 | **AI where ROI is clear** | SAM reads SQLite / charts; auto-analysis on `analysis-request` |

---

## Choose your venue

### A — **AWS live demo** (recommended for customers)

Stack is already on EC2 (Compose). You present in the browser; no terminal required unless something breaks.

1. Open the **live dashboard** (link above).
2. Confirm **Connected** badge and pipeline pulse counters (②③④) tick within ~30s.
3. Optional second screen: **Fleet chat (SAM)** tab or Slack channel with the bot invited.

**Do not** run a local `demo_publisher` against the same broker while EC2 traffic is on—you will duplicate readings and confuse counters.

### B — **Local laptop** (dry run / deep dive)

From `sam/`:

```bash
./start_traffic_generation.sh    # deadband, sketch, chart-writer, demo-publisher
./start_demo_stack.sh            # SAM :8000, chart-query :8010, anomaly, gateways
./healthcheck_demo_stack.sh
```

Open `sam/demo_dashboard.html` (or serve over HTTP). Point Chart API at `http://127.0.0.1:8010` and SAM at `http://127.0.0.1:8000`.

### C — **Deck + Pages only** (no broker)

Use [EXECUTIVE_DECK.html](EXECUTIVE_DECK.html) + [value story](https://mstobo.github.io/hvac-fleet-sam-demo/). Skip Acts 3–5 live; narrate from architecture diagram on Page 1.

---

## Pre-flight checklist (T−30 min)

### AWS / production path

- [ ] `docker compose ps` on EC2 — `deadband`, `sketch`, `chart-writer`, `demo-publisher`, `chart-query`, `sam-control-plane`, `anomaly` up.
- [ ] Dashboard loads; **Connected**; pulse line shows recent ② Filtered / ③ Sketched (not all `—`).
- [ ] **Fleet chat** tab loads SAM UI (or `:8000` / `/sam/` behind Apache).
- [ ] LLM proxy reachable from SAM container (`LLM_SERVICE_*` in `deploy/aws/.env`).
- [ ] If showing **Slack**: `slack` Compose profile up; bot in channel; `SLACK_ALERT_CHANNEL` set; test mention `@bot` once before the room arrives.
- [ ] Close duplicate browser tabs publishing from the twin (one traffic source is enough).
- [ ] Bump font size; hide email/Slack DMs; have [backup lines](#backup-if-something-fails) open on your phone.

### Demo-day tuning (optional — makes Act 4–5 easier)

In `deploy/aws/.env` (anomaly / fleet analyzer), consider:

```bash
# Easier to hit fleet-critical with 9 sensors (~3 of 9 instead of 5 of 9)
FLEET_CRITICAL_FRACTION=0.34
# Shorter wait before auto-analysis fires (default 60s feels broken in a live room)
ANALYSIS_DEBOUNCE_SECONDS=20
```

Restart `anomaly` (and stack if needed) after changing env. **Tell the audience** you shortened debounce for the demo so they are not waiting in silence.

### Local path

- [ ] `sam/.env` filled (`SOLACE_*`, `LLM_SERVICE_*`, `NAMESPACE`).
- [ ] `./healthcheck_demo_stack.sh` passes.
- [ ] `pip install -r requirements.txt` + SAM plugins per [README](../README.md).

### Recorded / remote presentation (SKO-style)

From the **May 18, 2026 SKO recording** (~29 min): the first minute was lost to **screen share not visible** while narration had already started (“here’s our HVAC fleet demo management” before Larry could see the screen).

**Before you speak on camera:**

1. **Share screen first** — entire desktop or one browser window with the live dashboard already loaded and **Connected**.
2. **Ask the room** — “Can you see the dashboard?” — before Act 1.
3. **Have a visible anchor** — live EC2 URL or Event Portal domain on screen; avoid audio-only assumptions.
4. **Recorder tip** — in Teams, confirm the shared window (not a blank desktop) appears in the recording preview.

**Optional cold open (if using Event Portal):**  
Open the **HVAC Fleet Management** (or your export) domain → show events/topics → then switch to the live dashboard for Acts 2–5. Keeps governance and runtime in one story.

---

## Screen layout (suggested)

| Screen 1 | Screen 2 (optional) |
|----------|---------------------|
| Live dashboard — **Pipeline · 3 columns** | SAM Fleet chat or Slack |
| Switch to **2D digital twin** for Act 4 | GitHub Pages value story |

---

## Act-by-act script

### Act 0 — Hook (1 min)

**Say:**  
“Facility telemetry often arrives every second. The expensive mistake is treating MQTT as an LLM input stream. This demo separates a **zero-LLM data plane** from a **control plane** that reasons on demand.”

**Show:** Token-burn diagram on [value story](https://mstobo.github.io/hvac-fleet-sam-demo/) or executive deck slide 1.

---

### Act 1 — Architecture in one picture (2 min)

**Say:**  
“**DC1** bridges raw readings to the **Hub**. Ingress is versioned MQTT: `dc/<site>/v1/raw/…`. Deterministic services publish `filtered`, `sketched`, and `alerts` under `dc/<site>/v1/pipeline/*`. SAM and gateways sit above—not in—the hot path.”

**Show:** Pages architecture block or [ARCHITECTURE_DECK.html](ARCHITECTURE_DECK.html).

**Prove:** Name the two planes aloud: data plane (no LLM per reading) vs control plane (Fleet Query + optional Slack + fleet-analysis gateway).

**Skip if short:** Java MQTT5 path; Event Portal unless the room asks.

---

### Act 2 — Protect spend + signal over noise (4–5 min)

**Show:** Dashboard → **Pipeline · 3 columns** tab.

**Say:**  
“Column 1 is **raw**—every simulated sensor tick. Most never deserve attention. Watch column 2 (**filtered**): deadband drops noise and heartbeats unchanged readings. Column 3 (**sketched**) only sees what mattered—template text summaries, not ChatGPT on each tick.”

**Prove:**

- Point at **pipeline pulse**: ② Filtered and ③ Sketched update; rates are far below raw.
- Pick one **suppressed** vs **forwarded** card if visible in column 1/2.

**Narration detail (technical room):**  
`deadband_service.py` → `sketch_service.py` → SQLite sketches for SAM tools. ~99% reduction is an order-of-magnitude story from deadband + heartbeat, not a guarantee.

**Common pitfall:** Pulse shows `—` → broker not connected or pipeline containers down. Do not improvise LLM; fix connection or switch to deck backup.

---

### Act 3 — Sketch, don’t spell out (2 min)

**Say:**  
“Sketches are **deterministic templates**—operator-readable lines built from rolling stats. Same *compress the story* idea as Sketch-of-Thought research, but here there is **no generative model** on the stream.”

**Show:** A sketched card in column 3 (zone, delta language).

**Prove:** Open README or `sketch_service.py` only if asked—keep focus on dashboard cards.

---

### Act 4 — Rules before models (3–4 min)

**Say:**  
“Anomaly detection is **thresholds and fleet math**, not inference. WARNING/CRITICAL zones come from configured temps. **Fleet critical** means enough sensors in CRITICAL at once—then we escalate.”

**Show:**

1. Stay on pipeline columns; mention column 3 can show **purple-bordered** sketch escalations.
2. Column 4 (**Anomaly alerts**) is **only** from `anomaly_service` on `…/pipeline/alerts`—if it is empty while column 3 is hot, say: “Sketch saw stress; rules haven’t fired fleet-level alert yet—that’s intentional separation.”

**Drive an alert (pick one):**

| Method | Good for |
|--------|----------|
| **Wait** for `demo_publisher` scenario (rack drift, power event) | Hands-off; can take several minutes |
| **2D digital twin** tab — raise 3+ motor/inlet sliders into red zone | Controlled; best for scheduled demos |
| Lower `FLEET_CRITICAL_FRACTION` pre-flight | Repeatable fleet-critical |

**Say when fleet-critical hits:**  
“Rules declared a fleet event. Slack may get a deterministic post. **Auto-analysis** waits for a short debounce, then publishes `sensors/fleet/analysis-request`—that is the ROI gate for the LLM.”

**Prove:** Pulse ④ Anomaly alerts ticks; optional Slack card in channel.

---

### Act 5 — AI where ROI is clear (3–5 min)

**Path 1 — Operator chat (reliable)**

**Show:** **Fleet chat (SAM)** tab or http://localhost:8000

**Prompts (copy-paste friendly):**

- `What is the current fleet status? Which sensors are in WARNING or CRITICAL?`
- `Summarize recent anomalies for machine-003.`
- `Plot m3-temp-motor for the last hour.` (needs chart-query healthy)

**Say:**  
“SAM tools query **SQLite and chart HTTP**—not raw MQTT. The model orchestrates; the numbers are already curated.”

**Path 2 — Event-triggered analysis (impressive when it works)**

**Say:**  
“After fleet-critical, `fleet_alert_analyzer` debounces, then publishes **one** analysis request. The fleet-analysis gateway routes to FleetQueryAgent; `analysis_response_to_slack` posts the narrative.”

**Prove:** Slack thread or SAM logs; mention ~20–60s debounce unless you tuned `ANALYSIS_DEBOUNCE_SECONDS`. See **[Debounce explained](#debounce-explained-automated-fleet-analysis)** below if the room asks what happens during the wait.

**If the room is mixed business/technical:** Stay on Path 1; describe Path 2 without waiting.

---

## Debounce explained (Automated Fleet Analysis)

Use this when someone asks “why is nothing happening for a minute?” or “is the LLM running already?”

### What triggers it

`anomaly_service` periodically recomputes **fleet status**. When enough sensors are in **CRITICAL** at once (default **≥ 50%** of active sensors, `FLEET_CRITICAL_FRACTION`), status becomes **`FLEET_CRITICAL`**.

That calls `fleet_alert_analyzer.on_fleet_critical()` — **only** for correlated fleet events, not a single hot sensor.

Per-sensor CRITICAL alerts can also call `on_sensor_critical()` during the window to add temperature/detail rows to the batch.

### During the debounce window (default **60s**, `ANALYSIS_DEBOUNCE_SECONDS`)

**The LLM is not running yet.** The anomaly container is:

1. Starting a **one-shot timer** for `DEBOUNCE_SECONDS`.
2. **Collecting in memory:**
   - fleet snapshots (counts, notes, `affected_sensor_ids`), and
   - optional per-sensor CRITICAL rows that arrive while the timer is open.
3. **Not resetting the timer** if another `FLEET_CRITICAL` evaluation fires mid-window — events are appended to the same batch.
4. Applying a **rate limit** before starting: if auto-analysis ran within the last **5 minutes** (`ANALYSIS_RATE_LIMIT_SECONDS`, default 300), the batch is dropped.

**Why:** avoid paying for an LLM call on a spike that clears in a few seconds; batch correlated context into **one** request.

**Say (30 seconds):**  
“We already alerted you deterministically. Debounce is a quiet minute to collect everything that happened across the fleet, then we ask SAM **once**.”

### When the timer fires

After the wait, `fleet_alert_analyzer`:

1. Builds **one** JSON event (`FLEET_CRITICAL_ANALYSIS_REQUEST`).
2. Publishes to MQTT **`sensors/fleet/analysis-request`** (QoS 1, broker ack).
3. Optionally publishes a **sketch audit report** to `sensors/fleet/audit-report` (deterministic SQLite lookback — not LLM).
4. **Fleet-analysis gateway** (in `sam-control-plane` on AWS) routes the request to **FleetQueryAgent** — **LLM work starts here**.
5. Response on **`sensors/fleet/analysis-response`** → **`analysis-to-slack`** posts the long *Automated Fleet Analysis* message.

### Timeline (what the audience sees)

| Time | What happens |
|------|----------------|
| **T+0** | `FLEET_CRITICAL` declared; deterministic **fleet Slack alert** may post immediately (often mentions “~60s debounce, then LLM”). |
| **T+0 → T+60s** | Pipeline still live; debounce **collecting**; logs: `Starting 60s debounce…`, `Added to pending batch`. **No SAM/LLM yet.** |
| **T+60s** | Single **`analysis-request`** published to the broker. |
| **T+60s + 1–5 min** | FleetQueryAgent runs; **Automated Fleet Analysis** Slack message (LLM latency). |

### What debounce is *not*

- Not re-running deadband/sketch on MQTT.
- Not blocking the dashboard or per-sensor rule alerts.
- Not the model “thinking” for 60 seconds — it has not been invoked yet.

### Demo tuning (`deploy/aws/.env` on EC2)

```bash
ANALYSIS_DEBOUNCE_SECONDS=20      # shorter wait for live rooms (default 60)
ANALYSIS_RATE_LIMIT_SECONDS=300  # minimum gap between auto-analyses
FLEET_CRITICAL_FRACTION=0.34     # easier to hit fleet-critical (~3/9 sensors)
ENABLE_AUTO_ANALYSIS=true
```

Restart **`anomaly`** (or full stack) after changing env.

**CLI test (no wait):** `python src/fleet_alert_analyzer.py --now` (from image or dev tree) — see module help.

### Presenter line if Slack is quiet

“The first message is the rule-based fleet alert. Automated analysis is **queued** — give it the debounce window plus a few minutes for the LLM. If we’re short on time, I’ll ask SAM directly in the Web UI instead.”

---

### Act 6 — Close (1 min)

**Say:**  
“Takeaways: **(1)** Versioned MQTT and bridge ingress. **(2)** Deterministic pipeline caps cost and latency. **(3)** Sketches and DB state prepare humans and agents. **(4)** LLM on **questions** and **fleet-critical events**—not on every reading. **(5)** Solace ties ingest, fan-out, and Agent Mesh together.”

**Invite:** [Value story](https://mstobo.github.io/hvac-fleet-sam-demo/) for executives; README for builders; live URL for hands-on.

---

## Known rough edges (say them before they bite you)

| Symptom | Likely cause | What to do live |
|---------|----------------|-----------------|
| Dashboard **“no data yet”** | WS creds / VPN / not connected | Connect panel; use pre-generated `demo_dashboard.config.json` on EC2 |
| ②③ pulse dead | Pipeline containers down | `docker compose restart deadband sketch demo-publisher` |
| Column 3 hot, column 4 empty | Sketch escalation ≠ anomaly alert topic | Explain; drive twin into CRITICAL or wait for fleet fraction |
| Fleet-critical but **no LLM** | Debounce (60s default), rate limit (5 min), or `ENABLE_AUTO_ANALYSIS=false` | Narrate timer; use SAM chat instead; tune env for demo day |
| SAM error / timeout | LLM proxy model name (`litellm_proxy/...`) or key | Fall back to data-plane story; show sketch cards |
| Slack silent | Socket Mode token, bot not in channel, gateway profile off | Use Web UI tab; show deterministic Slack from anomaly if configured |
| Duplicate / chaotic counters | Twin publishing **and** demo-publisher | Disable twin continuous publish when EC2 publisher runs |
| “Agents process every reading” | Old SAM-agent YAML story | Correct: **Python microservices** on MQTT; SAM is query + automation |

---

## Backup if something fails

| Failure | Line |
|---------|------|
| Broker auth | “Secrets stay in `.env`—never in slides. The architecture still holds.” |
| No alerts in time | “Publisher scenarios are probabilistic; I’ll drive the twin into CRITICAL to show the rule path.” |
| SAM down | “Control plane is optional to the cost story—watch filtered vs raw.” |
| Slack down | “Same SAM brain in the Web UI tab—Slack is just another gateway.” |
| LLM slow | “Analysis is debounced and rate-limited on purpose—ops-grade, not chat-grade volume.” |

---

## Timing cheat sheet

| Act | Minutes |
|-----|---------|
| 0 Hook | 1 |
| 1 Architecture | 2 |
| 2 Deadband / filter | 4–5 |
| 3 Sketch | 2 |
| 4 Rules / alerts | 3–4 |
| 5 SAM (+ optional auto) | 3–5 |
| 6 Close | 1 |
| **Total** | **15–20** |

**Short executive cut (10 min):** Acts 0, 1, 2 (pulse only), 5 (one SAM question), 6.

---

## Appendix — Local “four terminals” path (teaching only)

Use when explaining microservice boundaries—not for a customer AWS demo.

```bash
cd sam && source .venv/bin/activate
# T1
python src/demo_publisher.py
# T2–T4
python src/deadband_service.py
python src/sketch_service.py
python src/anomaly_service.py
```

**Say:** “Production and AWS use the same code paths via Compose; this layout is for learning.”

**Faster single process:** `python src/mock_pipeline.py` — same stages, one JVM-friendly laptop process.

---

## Appendix — SAM / Slack setup pointer

- Web UI: `run_sam_control_plane.sh` → port **8000**
- Slack: `sam plugin add slack …`, enable Compose profile `slack`, invite bot
- Fleet automation: `fleet-analysis-gateway` + topic `sensors/fleet/analysis-request`
- Details: [README § Quick start](../README.md), [sam/DEMO_QUICKSTART.md](../sam/DEMO_QUICKSTART.md) (SAM patterns; **not** the live pipeline implementation)

---

*Team Bubba — Rob Ottesen, Larry Norcini, Afshin Seysan, Matt Stobo. Update this script when EC2 URL or default ports change.*
