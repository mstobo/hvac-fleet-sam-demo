# Demo script — MQTT5 IoT analytics & HVAC-style pipeline

**Audience:** Technical or mixed (adjust depth live).  
**Goal:** Show how high-volume MQTT is tamed **before** any LLM, then how optional AI fits on top.  
**Duration:** ~12–18 minutes (stretch with Q&A).

**Presenter thread (keep in mind):** For SAM, **Web UI** and **Slack** are two ways into the same query plane. For a **small group demo**, one server-side **LLM** key plus **one** Slack app install is enough—invite the bot to a channel (or DMs); each person `@`-mentions and prompts from their own Slack; no per-person API keys.

---

## 0. Before you go live (2 min)

- [ ] Broker credentials in `sam/.env` or `SOLACE_*` env vars; confirm TLS port (typically **8883**).
- [ ] Python venv in `sam/` with `pip install -r requirements.txt`.
- [ ] Optional: open `sam/demo_dashboard.html` in a browser (digital twin + live MQTT pulses).
- [ ] Optional: **Slack** — one app, one `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` in `.env`; enable `slack` Compose profile or include `configs/gateways/slack-bot.yaml`. Invite the bot to a channel (or DMs) so guests can `@`-prompt without per-person keys (see **Presenter thread** above).
- [ ] Optional: Solace Event Portal domain **HVAC Fleet Management** (or your export) for the “documented EDA” beat.
- [ ] Close unrelated tabs; bump terminal font size for the room.

**One-liner to memorize:**  
“We don’t stream every reading to an LLM. We **filter, summarize, and rule-detect** on the broker path, store curated state in SQLite, and only then let agents or humans ask questions.”

---

## 1. Opening — problem & promise (~90 s)

**Say:**

> Industrial and facility telemetry often arrives every second or faster on MQTT. The naive pattern—“ship it all to an LLM”—blows up cost and adds noise.  
> This project shows a **data plane** that runs entirely **without** model calls: deadband, rolling-window stats, natural-language **sketches** (template text, not generative AI), and threshold-based alerts.  
> Optional **Solace Agent Mesh** and a **query plane** sit on top: the model reads **curated** sketches and alerts from SQLite, not raw firehose. If you show SAM, call out that **several people** can ask questions through the **Web UI or Slack** once the stack is running—credentials live on the server, not with each guest.

**Gesture:** Point at README diagram or Page 1 of `ARCHITECTURE_DECK.html`.

---

## 2. Architecture map (~2 min)

**Data plane (no LLM):**

1. **Ingress** — Raw readings on versioned topics under `dc/<brokerSite>/v1/raw/...` (config: `sam/src/pipeline_config.py`). Simulators: `sam/src/demo_publisher.py`; optional **digital twin** in `sam/demo_dashboard.html` (same topic shape, or °C **in** the topic when the twin checkbox is on).
2. **Deadband** — `deadband_service.py`: suppresses small deltas, heartbeat-forward, zone classification (NORMAL / WARNING / CRITICAL).
3. **Sketch** — `sketch_service.py`: turns forwarded readings into short NL summaries; persists batches to SQLite.
4. **Anomaly** — `anomaly_service.py`: rule-based alerts; fleet aggregation; optional Slack / fleet analyzer hooks.

**Topics to name-check (write on whiteboard or show deck):**

- Raw: `dc/<site>/v1/raw/{site}/{room}/{row}/{rack}/{asset}/{metric}`
- Internal: `.../pipeline/filtered`, `.../pipeline/sketched`, `.../pipeline/suppressed`, `.../pipeline/alerts`
- Routed events: `.../v1/event/...` (from anomaly service when alerts fire)

**Control plane (optional in demo):**

- SAM + tools over SQLite (`fleet_query_tools` pattern in README). **Entry points:** mesh **Web UI** (browser) or **Slack** bot (shared channel / DMs)—same backend; Slack is ideal when a few people take turns prompting without sharing LLM keys.
- **Java path** (separate story): `MQTT5Publisher` / `MQTT5Subscriber` + optional JSON Schema / SERDES — `test/mqtt5/messages` in `MqttConfig.java`; use if your audience cares about MQTT 5 + schema validation.

---

## 3. Live demo — data plane (~6–8 min)

**Terminal layout suggestion:** T1 publisher, T2–T4 three microservices **or** one `mock_pipeline.py` if you want a single process for a tight slot.

### Path A — Three microservices (preferred for “real” EDA)

```bash
cd sam
source .venv/bin/activate   # or Windows equivalent
```

**T1 — Sensor load (simulator):**

```bash
export SOLACE_HOST="…" SOLACE_PORT=8883 SOLACE_USER="…" SOLACE_PASS="…" SOLACE_TLS=true
# Optional: match your broker site segment
export DC_BROKER_SITE=Hub
python src/demo_publisher.py
```

**T2 — Deadband:** `python src/deadband_service.py`  
**T3 — Sketch:** `python src/sketch_service.py`  
**T4 — Anomaly:** `python src/anomaly_service.py`

**Narrate while logs scroll:**

- “**SUPPRESS**” lines → deadband doing its job (noise not forwarded).
- **FORWARD** with zone → sketch will receive; watch **sketched** topic or SQLite growth if you tail DB.
- When a sensor hits WARNING/CRITICAL → **ALERT** JSON on `.../pipeline/alerts` and hierarchical `.../v1/event/...`.

**Optional — Digital twin (1 min):**

- Open `demo_dashboard.html` (serve from `sam/` or open file per your setup).
- Move a slider; show **raw** pulse and downstream pulses if services run.
- Toggle **temperature in topic** vs JSON-only — tie to Event Portal “two raw shapes” if you showed Portal earlier.

### Path B — Single combined process (faster setup)

```bash
python src/mock_pipeline.py
```

**Say:** “Same stages, one process—good for laptops; production would split services.”

---

## 4. Optional beats (pick one, ~2–3 min)

**A — Cost / ROI** (README numbers): contrast naive “all readings to LLM” vs deterministic front + rare queries.

**B — SAM query plane:** `sam run`, open mesh Web UI (port 8000 by default), ask e.g. “What’s fleet status?” — emphasize tools hit **SQLite**, not raw MQTT. **Slack (same idea, shared audience):** with the Slack gateway running, the bot is just another client into SAM: it routes messages to **FleetQueryAgent** (see `sam/configs/gateways/slack-bot.yaml`). One set of Slack credentials on the server is enough for a **small group**—each person prompts from their own Slack client; access is whatever your workspace/channel rules allow, not separate LLM keys per person.

**C — Event Portal:** Show **HVAC Fleet Management** — events mapped to topics, microservices as declared producers/consumers, dashboard on raw variants.

**D — Java MQTT 5 + schema:** Short compile/run of `MQTT5Publisher` / `MQTT5Subscriber` if the room is Java-heavy.

---

## 5. Close (~60 s)

**Say:**

> Takeaways: **(1)** Versioned MQTT taxonomy and schema metadata in payloads. **(2)** Deterministic pipeline reduces volume and prepares human- and model-readable context. **(3)** LLM is **optional** and **query-side**, not in the hot path. **(4)** Solace ties it together—fan-out, ordering, and Event Portal for governance. **(5)** For live audiences, **Slack + one bot** (or the Web UI) lets multiple people try prompts without handing out secrets.

**Invite:** Questions; link to repo README and `demo/ARCHITECTURE_DECK.html`.

---

## Backup lines (if something fails)

- Broker auth error → “This is why we keep secrets in `.env`, not in slides.”
- No alerts → explain zones and thresholds (`WARNING_TEMP` / `CRITICAL_TEMP` in `pipeline_config.py`); offer to lower deadband or run twin preset.
- SAM won’t start → stay on data plane + deck; “control plane is optional today.”
- Slack bot silent / disconnected → check Socket Mode **app** token, bot invited to the channel, and gateway container running (`slack` Compose profile); guests only need Slack access, not your `.env`.

---

## Timing cheat sheet

| Block        | ~Minutes |
|-------------|----------|
| Intro       | 1.5      |
| Architecture| 2        |
| Live demo   | 6–8      |
| Optional    | 2–3      |
| Close       | 1        |
| **Total**   | **12–15**|
