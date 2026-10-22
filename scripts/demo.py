"""Phase 10 demo: clean answer, not covered, disagreement, PII, refinement, approval."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eval.demo import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
