# Content Strategy — Blog + Demo First, LinkedIn as Distribution

**Goal:** One credible “source of truth” people can bookmark, then short LinkedIn posts that **drive traffic** to it—not eight standalone essays that repeat the same story.

**Positioning (one sentence):**  
We process industrial telemetry on an **event-driven, deterministic data plane** and invoke AI only on **curated state and fleet-critical exceptions**—with measured token economics to prove it.

**Primary audience:** Architects and engineering leaders evaluating IIoT + AI; secondary: facilities/reliability leaders who feel pilot→production bill shock.

---

## Funnel (hub and spokes)

```text
                    ┌─────────────────────────────┐
                    │  PILLAR A: Long-form blog   │
                    │  (own the narrative + SEO)  │
                    └──────────────┬──────────────┘
                                   │
         ┌─────────────────────────┼─────────────────────────┐
         ▼                         ▼                         ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ PILLAR B: Demo  │    │ GitHub Pages    │    │ Repo / README   │
│ video (10–15m)  │    │ value story     │    │ technical depth │
└────────┬────────┘    └────────┬────────┘    └────────┬────────┘
         │                      │                      │
         └──────────────────────┼──────────────────────┘
                                ▼
                    ┌─────────────────────────────┐
                    │  LinkedIn: 6–10 micro-posts │
                    │  each → ONE link + one idea │
                    └─────────────────────────────┘
```

**Rule:** Every LinkedIn post has **one CTA link** (blog *or* video timestamp *or* repo section)—never “read my last 7 posts to get the picture.”

---

## Phase 0 — Decide anchors (½ day)

| Decision | Recommendation |
|----------|----------------|
| **Blog host** | GitHub Pages article under `docs/` (same site as value story) *or* LinkedIn Article *or* company blog—pick **one canonical URL** for backlinks. |
| **Blog title (working)** | *Reason on the Exception: An Event-Driven Architecture for IIoT AI That Survives Production Bills* |
| **Video host** | YouTube (unlisted until launch) or LinkedIn native video—YouTube better for embeds in blog; LI native better for LI algorithm. **Do both if cheap:** upload once, cross-post. |
| **Video length** | **12–15 min** full demo; cut **60–90 sec** teaser for LI. |
| **Canonical demo URL** | Live EC2 dashboard + traceable FLEET_CRITICAL → Slack (from `demo/DEMO_SCRIPT.md`). |

---

## Phase 1 — Pillar A: Blog (week 1–2)

**Purpose:** The article people share in Slack threads and architecture reviews. LinkedIn posts are **footnotes** to this.

### Suggested structure (~1,800–2,500 words)

1. **Hook** — Pilot succeeds; production token bill arrives (use whitepaper math, one table).
2. **Wrong pattern** — Sensor → LLM; why reasoning engines aren’t stream processors.
3. **Right pattern** — Data plane (deadband, sketch, rules, fleet fraction) vs agent on SQLite/charts.
4. **Sketches** — Deterministic compression; NL vs jargon; link to Sketch-of-Thought as *related*, not identical.
5. **Case study** — Fleet SECTION A: tool budget (3+3+1), report sections, three machine charts.
6. **Measurements** — ~196k NL / ~135k jargon / ~308k context-heavy; what moved input vs output.
7. **Replay & snapshots** — Replay doesn’t save tokens unless you republish an **artifact**; stale vs point-in-time.
8. **Checklist** — 5 production habits (sketch cap, debug off, tool budget, idempotent analysis, label snapshots).
9. **CTA** — Repo, live demo, “watch the walkthrough” (video link added in Phase 2).

### Where it lives in repo

| Option | Path | Pros |
|--------|------|------|
| **A (recommended)** | `docs/blog/reason-on-the-exception.html` + `.md` in repo | **Live** — push `main` with Pages folder `/docs` |
| B | External Medium/Substack | Reach; splits canonical URL |
| C | LinkedIn Article only | LI reach; weak for developers bookmarking |

### Blog production checklist

- [ ] One architecture diagram (data plane vs SAM)—reuse/adapt `ARCHITECTURE_DECK` / Pages assets.
- [ ] One table: token runs (NL / jargon / heavy context).
- [ ] One code-free “SECTION A tool budget” box.
- [ ] Screenshots: Slack footer tokens, dashboard NL/Jargon toggle, three chart links in report.
- [ ] Footer links: GitHub repo, `deploy/aws/README.md`, arXiv SoT (optional).

---

## Phase 2 — Pillar B: Demo video (week 2–3)

**Purpose:** Show the blog is real—not slideware. Viewers should recognize the **same flow** as section 5–6 of the blog.

### Recommended acts (map to `demo/DEMO_SCRIPT.md`)

| Min | Act | Blog section it proves |
|-----|-----|-------------------------|
| 0:00 | Problem (10 sensors vs 10,000 tags) | §1–2 |
| 1:30 | Dashboard: pipeline counters, deadband suppress vs forward | §3 |
| 4:00 | Sketches in DB / jargon toggle | §4 |
| 6:00 | FLEET_CRITICAL preset → debounce → Slack short alert | §5 |
| 8:30 | Full **Automated Fleet Analysis** (sections 1–8, 3 machine charts, token footer) | §5–6 |
| 11:00 | Optional: WebUI fleet chat (contrast—operator question, not automation) | §2 |
| 12:30 | Wrap: replay/snapshot **one slide**, no live replay required | §7 |
| 13:30 | CTA: blog URL + repo | — |

### Recording checklist

- [ ] Script from `DEMO_SCRIPT.md` Act A (AWS live)—rehearse once with `FLEET_CRITICAL` preset.
- [ ] Capture **1080p** browser + optional face cam; clean Slack channel for analysis message.
- [ ] Before/after or side-by-side: NL vs jargon footer (two runs **or** one run + lab screenshot).
- [ ] Export **60–90 s** teaser: FLEET_CRITICAL → Slack report + token line.
- [ ] Chapters/timestamps in YouTube description (blog embeds them).

### Video ↔ blog linking

- Blog embeds: `https://youtube.com/...` at top (“Watch 13 min”) and after §5.
- Video description: **first link** = canonical blog URL; second = live demo; third = GitHub.

---

## Phase 3 — LinkedIn distribution (week 3–6, after pillars ship)

**Purpose:** Discovery and commentary—not primary education.

### Post types (all link outward)

| # | LI post role | Links to | ~Length |
|---|--------------|----------|---------|
| 1 | Launch: “We published the architecture story” | **Blog** | Short + link preview |
| 2 | Stat hook: 196k → 135k tokens | Blog §6 + video timestamp ?t=8m30s | Short |
| 3 | “Sketches aren’t LLM summaries on the stream” | Blog §4 | Carousel → blog |
| 4 | Tool budget meme: “agents investigate everything” | Blog §5 + video timestamp | Short |
| 5 | Myth bust: “Solace replay won’t cut your AI bill” | Blog §7 | Short |
| 6 | Snapshot vs live ops (incident narrative) | Blog §7 | Short |
| 7 | Demo drop: “13-min walkthrough” | **Video** | Short + native video |
| 8 | Repo / try it: EC2 + open source | GitHub Pages + repo | Short |

**Frequency:** 1–2×/week for 4 weeks after launch week—not 8 weeks of original long posts.

### LinkedIn post template

```text
[One sharp claim — 2 lines max]

[One proof — number, screenshot, or “we measured”]

Read the full architecture + token breakdown: [BLOG URL]
Watch the fleet-critical walkthrough: [VIDEO URL] (13 min)

[One question for comments]

#IIoT #EventDriven #IndustrialAI
```

### Profile setup (once)

- **Featured:** Blog (link) + Video (link).
- **Headline/bio:** One line from positioning sentence + link to Pages.

---

## Phase 4 — Optional extensions (later)

| Asset | When | Points to |
|-------|------|-----------|
| Executive PDF / deck | Customer meetings | Blog + video |
| Conference talk abstract | CFP season | Blog as reference |
| `tools/sketch-token-lab` post | Technical audience | Blog §4 appendix |
| Second blog: “10 vs 25 sketches A/B” | After you have two real Slack runs | First blog |

---

## Timeline (realistic solo founder / small team)

| Week | Deliverable |
|------|-------------|
| 1 | Blog outline → draft v1; gather screenshots + token footers |
| 2 | Blog published (canonical URL live); update Pages nav → “Architecture blog” |
| 3 | Record demo v1; edit; upload; embed in blog |
| 4 | LI launch post + 2 follow-ups (blog + video) |
| 5–6 | 4–6 more LI micro-posts; engage comments |
| 7+ | Second wave only if metrics justify (A/B sketch post, etc.) |

---

## Success metrics (keep it simple)

| Metric | Target signal |
|--------|----------------|
| Blog unique views | Baseline in 30 days |
| Video watch time | >40% avg for 13 min = right audience |
| LI click-through | CTR on blog link in posts 1–3 |
| Inbound | DMs/comments mentioning “token bill” or “tool budget” |
| Technical | GitHub stars/forks, demo healthcheck runs |

---

## What to retire from “LI-first” plan

The file [`LINKEDIN_BLOG_SERIES.md`](LINKEDIN_BLOG_SERIES.md) stays useful as **copy bank** (hooks, stats, guardrails)—but **publishing order** is:

1. Blog  
2. Video  
3. LinkedIn teasers → pillars  

Do **not** publish eight standalone LI articles before the blog exists; you’ll split SEO and repeat yourself.

---

## Immediate next actions (pick one block this week)

**Blog block**
- [ ] Approve title + host (Pages path).
- [ ] Draft §1–3 from `WHITEPAPER_EXECUTIVE_DRAFT.md` + demo one-liner.
- [ ] Schedule screenshot session (Slack footer NL + jargon, fleet report).

**Video block**
- [ ] Rehearse `DEMO_SCRIPT.md` Path A on EC2.
- [ ] List B-roll: dashboard counters, MQTT pulse, jargon toggle.

**LI block (only after URL exists)**
- [ ] Write launch post (template above) — **do not post** until blog URL works.

---

## Related repo docs

| Doc | Use |
|-----|-----|
| [`WHITEPAPER_EXECUTIVE_DRAFT.md`](../WHITEPAPER_EXECUTIVE_DRAFT.md) | Blog §1–2 economics |
| [`demo/DEMO_SCRIPT.md`](../demo/DEMO_SCRIPT.md) | Video shot list |
| [`LINKEDIN_BLOG_SERIES.md`](LINKEDIN_BLOG_SERIES.md) | Micro-post copy bank |
| [`docs/index.html`](index.html) / Pages | Add blog + video to nav |
| [`docs/FLEET_ANALYSIS_PRODUCTION.md`](FLEET_ANALYSIS_PRODUCTION.md) | Runbook linked from blog (checklist moved out of post) |
