# Sketch token lab (NL vs Expert Lexicon)

Offline comparison tool for [Sketch-of-Thought](https://arxiv.org/abs/2503.05179) **Expert Lexicons** vs natural-language sketches/prompts in the HVAC fleet demo context.

## What this is / isn't

| | |
|--|--|
| **Is** | Token estimator for sketch *text* and short fleet system prompts using `tiktoken` |
| **Isn't** | A change to `sketch_service.py` (production sketches stay deterministic Python, no LLM) |
| **Isn't** | Full SoT routing (Conceptual Chaining / Chunked Symbolism / DistilBERT router) |

SoT reduces **LLM reasoning trace** tokens. Your demo already avoids LLM cost on sketch *generation*; this lab answers: *if the agent reads 25×3 sketches in jargon form, how many input tokens do we save vs today's NL sketches?*

## Quick start

```bash
cd tools/sketch-token-lab
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python compare.py
```

Options:

```bash
python compare.py --sketches 25 --machines 3 --model gpt-4o
python compare.py --export-json /tmp/sketch-token-report.json
```

## Interpreting output

- **single_sketch** — one MQTT sketch line (NL vs jargon).
- **incident_bundle_25_sketches** — one `get_incident_context` payload sketch block.
- **fleet_3_machines_x_25** — current good path (3 machine-level context calls).
- **legacy_9_points_x_25** — old 9× per-point path (for before/after).
- **system_plus_fleet_sketches_3m** — rough lower bound on sketch-related *input* tokens (not full 193k fleet run).

Jargon style uses short codes: `m1:mot Δ↑6.2% T80.4 Z:C μ30=76.1[74-82] !CRIT`.

## Production toggle

Set on EC2 (or locally) in `deploy/aws/.env`:

```bash
SKETCH_STYLE=jargon
```

**Preferred (no restart):** dashboard **NL / Jargon** toggle or:

```bash
./deploy/aws/scripts/set-sketch-style.sh jargon deploy/aws/.env
```

**Legacy:** set `SKETCH_STYLE=jargon` in `.env` and recreate **sketch** + **sam-control-plane**.

New sketches use the effective style; old rows in SQLite age out (~120m window).

## Live A/B

Run two fleet analyses (NL vs jargon) and compare Slack `LLM usage` footer totals.
