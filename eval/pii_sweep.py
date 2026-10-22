"""Search saved eval artifacts for synthetic PII. Expected hits: 0.

    python eval/pii_sweep.py eval/results/recorded
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from eval.runner import pii_hits  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: python eval/pii_sweep.py <results-dir>", file=sys.stderr)
        return 2
    root = Path(args[0])
    hits = pii_hits(root, extra_values=["ABCDE1234F"])
    print(json.dumps(hits, indent=2))
    print(f"hits: {len(hits)}")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
