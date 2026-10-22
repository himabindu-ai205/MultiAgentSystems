"""Run the Phase 10 acceptance suites and write evidence under eval/results/.

    python eval/run_eval.py --suite all --mode recorded
    python eval/run_eval.py --suite e1
    python eval/run_eval.py --suite all --mode live
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from config import load_settings  # noqa: E402
from data_layer import load_all  # noqa: E402
from eval.fixtures import RESULTS_DIR  # noqa: E402
from eval.runner import RULES_ONLY, run_acceptance  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the pre-submission acceptance checks.")
    parser.add_argument("--suite", default="all", choices=("all", "e1", "e2", "e3"))
    parser.add_argument("--mode", default="recorded", choices=("recorded", "live"))
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    settings = RULES_ONLY if args.mode == "recorded" else load_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    suffix = "" if args.mode == "recorded" else "-live"
    out = Path(args.out) if args.out else RESULTS_DIR / f"{stamp}{suffix}"
    if args.mode == "recorded" and not args.out:
        out = RESULTS_DIR / "recorded"

    result = run_acceptance(settings, data=load_all(), out_dir=out)
    print(f"wrote {result.out_dir}")
    print((result.out_dir / "summary.md").read_text(encoding="utf-8"))
    if args.suite != "all":
        print((result.out_dir / f"{args.suite}.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
