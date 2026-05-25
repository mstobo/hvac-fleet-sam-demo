#!/usr/bin/env python3
"""
Compare token cost: natural-language vs Expert-Lexicon-style sketches/prompts.

Offline by default (tiktoken). Inspired by Sketch-of-Thought (arXiv:2503.05179).

Usage:
  pip install -r requirements.txt
  python compare.py
  python compare.py --sketches 25 --machines 3
  python compare.py --model gpt-4o --export-json report.json

Note: sam/src/sketch_service.py does NOT call an LLM today — this lab estimates
what you would pay IF sketches or fleet prompts were phrased differently in
agent context (get_incident_context, fleet gateway, etc.).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from typing import Dict, List

from styles import (
    FLEET_SYSTEM_PROMPT_JARGON,
    FLEET_SYSTEM_PROMPT_NL,
    bundle_sketches,
    demo_event_stream,
    sketch_jargon,
    sketch_nl,
)


def count_tokens(text: str, model: str) -> int:
    """BPE count via tiktoken when available; else ~chars/4 estimate."""
    text = text or ""
    try:
        import tiktoken

        try:
            enc = tiktoken.encoding_for_model(model)
        except KeyError:
            enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except ImportError as exc:
        raise SystemExit("Install tiktoken: pip install -r requirements.txt") from exc
    except Exception:
        # Offline / blocked encoding download: coarse estimate (good for A/B ratios).
        return max(1, int(len(text) / 4))


@dataclass
class ScenarioResult:
    name: str
    nl_tokens: int
    jargon_tokens: int

    @property
    def saved(self) -> int:
        return self.nl_tokens - self.jargon_tokens

    @property
    def pct_saved(self) -> float:
        if self.nl_tokens <= 0:
            return 0.0
        return 100.0 * self.saved / self.nl_tokens


def run_scenarios(
    *,
    sketches_per_bundle: int,
    machines: int,
    model: str,
) -> List[ScenarioResult]:
    events = demo_event_stream(sketches_per_bundle)
    results: List[ScenarioResult] = []

    single_nl = sketch_nl(events[0])
    single_jargon = sketch_jargon(events[0])
    results.append(
        ScenarioResult(
            "single_sketch",
            count_tokens(single_nl, model),
            count_tokens(single_jargon, model),
        )
    )

    bundle_nl = bundle_sketches(events, "nl")
    bundle_jargon = bundle_sketches(events, "jargon")
    results.append(
        ScenarioResult(
            f"incident_bundle_{sketches_per_bundle}_sketches",
            count_tokens(bundle_nl, model),
            count_tokens(bundle_jargon, model),
        )
    )

    # Fleet SECTION A: 3 machine-level context bundles (current good path).
    fleet_nl = "\n\n".join(bundle_sketches(demo_event_stream(sketches_per_bundle), "nl") for _ in range(machines))
    fleet_jargon = "\n\n".join(
        bundle_sketches(demo_event_stream(sketches_per_bundle), "jargon") for _ in range(machines)
    )
    results.append(
        ScenarioResult(
            f"fleet_{machines}_machines_x_{sketches_per_bundle}_sketches",
            count_tokens(fleet_nl, model),
            count_tokens(fleet_jargon, model),
        )
    )

    # Legacy bad path: 9 point-level bundles × 25 sketches.
    nine_nl = "\n\n".join(bundle_sketches(demo_event_stream(sketches_per_bundle), "nl") for _ in range(9))
    nine_jargon = "\n\n".join(bundle_sketches(demo_event_stream(sketches_per_bundle), "jargon") for _ in range(9))
    results.append(
        ScenarioResult(
            f"legacy_9_points_x_{sketches_per_bundle}_sketches",
            count_tokens(nine_nl, model),
            count_tokens(nine_jargon, model),
        )
    )

    results.append(
        ScenarioResult(
            "fleet_system_prompt",
            count_tokens(FLEET_SYSTEM_PROMPT_NL, model),
            count_tokens(FLEET_SYSTEM_PROMPT_JARGON, model),
        )
    )

    combined_nl = FLEET_SYSTEM_PROMPT_NL + "\n\n--- sketches ---\n" + fleet_nl
    combined_jargon = FLEET_SYSTEM_PROMPT_JARGON + "\n\n--- sketches ---\n" + fleet_jargon
    results.append(
        ScenarioResult(
            f"system_plus_fleet_sketches_{machines}m",
            count_tokens(combined_nl, model),
            count_tokens(combined_jargon, model),
        )
    )

    return results


def print_report(results: List[ScenarioResult], model: str) -> None:
    print(f"\nSketch token lab (encoding for model={model})\n")
    print(f"{'Scenario':<42} {'NL':>8} {'Jargon':>8} {'Saved':>8} {'%':>7}")
    print("-" * 76)
    for r in results:
        print(
            f"{r.name:<42} {r.nl_tokens:>8,} {r.jargon_tokens:>8,} "
            f"{r.saved:>8,} {r.pct_saved:>6.1f}%"
        )

    print("\nSample texts (same event):\n")
    e = demo_event_stream(1)[0]
    print("NL:")
    print(f"  {sketch_nl(e)}")
    print("\nJargon (SoT Expert-Lexicon style):")
    print(f"  {sketch_jargon(e)}")
    print(
        "\nReminder: production sketches use sketch_service.py (NL, zero LLM tokens)."
        "\nThis lab estimates downstream cost when sketches are read by FleetQueryAgent / SAM."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="NL vs jargon sketch token comparison")
    parser.add_argument("--sketches", type=int, default=25, help="Sketches per incident bundle")
    parser.add_argument("--machines", type=int, default=3, help="Machines in fleet scenario")
    parser.add_argument("--model", default="gpt-4o", help="tiktoken model name for encoding")
    parser.add_argument("--export-json", help="Write full results to JSON file")
    args = parser.parse_args()

    results = run_scenarios(
        sketches_per_bundle=args.sketches,
        machines=args.machines,
        model=args.model,
    )
    print_report(results, args.model)

    if args.export_json:
        payload = {
            "model": args.model,
            "sketches_per_bundle": args.sketches,
            "machines": args.machines,
            "results": [asdict(r) for r in results],
        }
        with open(args.export_json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"\nWrote {args.export_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
