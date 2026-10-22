"""Tuning report for the Pattern Agent (Implementation Plan Phase 3, task 6).

Prints, for the real data:

1. per-week review counts for each theme (newest week first),
2. how often each signal fires when every low-rated review is used as a complaint,
3. a few real examples of each signal.

Run from the project root:  python scripts/tune_pattern.py [min_count] [rising_ratio]

It prints review text (reviews contain no PII) and writes nothing.
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.pattern import PatternAgent  # noqa: E402
from config import load_settings  # noqa: E402
from constants import THEME_IDS  # noqa: E402
from data_layer import load_all  # noqa: E402


def main() -> None:
    settings = load_settings().pattern
    if len(sys.argv) > 1:
        settings = settings.model_copy(update={"min_count": int(sys.argv[1])})
    if len(sys.argv) > 2:
        settings = settings.model_copy(update={"rising_ratio": float(sys.argv[2])})

    data = load_all()
    agent = PatternAgent(data.themes, settings=settings)
    print(f"anchor_date={agent.anchor_date}  min_count={settings.min_count}  "
          f"rising_ratio={settings.rising_ratio}  window_days={settings.window_days}\n")

    print("Reviews per 7-day window, newest first")
    for theme_id in THEME_IDS:
        print(f"  {theme_id:<16}", agent.weekly_counts(theme_id, 12))

    signals: Counter[tuple[str, str]] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    for review in data.themes.reviews.values():
        if review.rating > 2:
            continue
        result = agent.run(review.text)
        signals[(result.basis, result.signal)] += 1
        if result.basis == "keyword_match" and len(examples[result.signal]) < 5:
            examples[result.signal].append(
                f"[{result.theme_id} {result.count_current} vs {result.count_prior}] "
                + review.text[:90].replace("\n", " ")
            )

    print("\nSignals when each 1-2 star review is used as a complaint")
    for key, count in sorted(signals.items()):
        print(f"  {key[0]:<14} {key[1]:<10} {count}")
    for signal, lines in examples.items():
        print(f"\nExamples of {signal} (keyword_match)")
        for line in lines:
            print("  ", line)


if __name__ == "__main__":
    main()
