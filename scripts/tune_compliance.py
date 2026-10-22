"""Tuning report for the Compliance Agent (Implementation Plan Phase 4).

Prints:

1. advice fixtures that were NOT flagged inbound (must be none),
2. how many real reviews the inbound rules flag, per rule, with a few examples,
3. corpus text blocks that the outbound rules flag (must be none).

Run from the project root:  python scripts/tune_compliance.py [examples_per_rule]

It prints review text (reviews contain no PII) and writes nothing.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agents.compliance import ComplianceAgent  # noqa: E402
from data_layer import load_all  # noqa: E402


def main() -> None:
    shown = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    data = load_all()
    agent = ComplianceAgent(corpus=data.corpus)

    prompts = json.loads((ROOT / "tests" / "fixtures" / "advice_prompts.json").read_text(encoding="utf-8"))
    missed = [
        text
        for group in prompts["groups"]
        for text in group["prompts"]
        if agent.inbound(text).clean
    ]
    print(f"advice fixtures not flagged: {len(missed)}")
    for text in missed:
        print("  MISS:", text)

    by_rule: dict[str, list[str]] = defaultdict(list)
    reviews = [r.text for r in data.themes.reviews.values()]
    flagged = 0
    for review in reviews:
        result = agent.inbound(review)
        if result.flags:
            flagged += 1
            for flag in result.flags:
                by_rule[flag.detail].append(review)
    print(f"\nreal reviews flagged inbound: {flagged} of {len(reviews)} ({flagged / max(len(reviews), 1):.1%})")
    for code, texts in sorted(by_rule.items(), key=lambda kv: -len(kv[1])):
        print(f"\n{code}: {len(texts)}")
        for text in texts[:shown]:
            print("   -", text[:160].replace("\n", " "))

    blocks = [
        block.text
        for scheme in data.corpus.schemes.values()
        for block in scheme.text_blocks
        if "management" not in block.section_title.lower()
    ]
    bad = [b for b in blocks if not agent.outbound(b).clean]
    print(f"\ncorpus blocks flagged outbound: {len(bad)} of {len(blocks)}")


if __name__ == "__main__":
    main()
