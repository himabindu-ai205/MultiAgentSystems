"""Fixed inputs for the Phase 10 checks (eval.md E1–E3)."""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

from constants import THEME_IDS
from data_layer.themes import Themes
from ingress.redactor import find_pii

EVAL_DIR = Path(__file__).resolve().parent
SETS_DIR = EVAL_DIR / "sets"
RESULTS_DIR = EVAL_DIR / "results"
SEED = 20261021
PER_THEME = 4
SHORT_CHARS = 40
LONG_CHARS = 200

HINGLISH = re.compile(
    r"[\u0900-\u097F]|\b(kya|nahi|nahin|bahut|accha|acha|achha|theek|mujhe|"
    r"mera|meri|kitna|kyun|kyunki|karo|karna|paise|paisa|bhai|yaar|krdo)\b",
    re.I,
)
FEES = re.compile(r"\b(fee|fees|brokerage|charge|charges|charged|gst)\b", re.I)
KYC = re.compile(
    r"\b(kyc|withdraw|withdrawal|withdrawals|payment|payments|upi|payout|refund)\b", re.I
)
NEEDED = {"hinglish": 4, "fees": 2, "kyc": 2, "short": 2, "long": 2}


def _load_json(name: str) -> Any:
    path = SETS_DIR / name
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_e1() -> dict[str, Any]:
    return _load_json("e1_ids.json")


def load_branches() -> list[dict[str, Any]]:
    return _load_json("e2_branches.json")["branches"]


def load_advice() -> dict[str, Any]:
    return _load_json("e3_advice.json")


def tags_for(text: str) -> frozenset[str]:
    found: set[str] = set()
    if HINGLISH.search(text):
        found.add("hinglish")
    if FEES.search(text):
        found.add("fees")
    if KYC.search(text):
        found.add("kyc")
    if len(text) < SHORT_CHARS:
        found.add("short")
    if len(text) > LONG_CHARS:
        found.add("long")
    return frozenset(found)


def select_e1(themes: Themes, *, seed: int = SEED, per_theme: int = PER_THEME) -> list[dict[str, Any]]:
    """Pick ``per_theme`` reviews from each theme. Prefer low ratings and fill the mix quotas."""
    rng = random.Random(seed)
    remaining = dict(NEEDED)
    picked: list[dict[str, Any]] = []
    used: set[str] = set()
    pools: dict[str, list] = {}
    for theme_id in THEME_IDS:
        eligible = [
            themes.reviews[rid]
            for rid in themes.themes[theme_id].review_ids
            if not find_pii(themes.reviews[rid].text)
        ]
        low = [r for r in eligible if r.rating <= 3]
        high = [r for r in eligible if r.rating > 3]
        rng.shuffle(low)
        rng.shuffle(high)
        pools[theme_id] = low + high

    left = {tid: per_theme for tid in THEME_IDS}
    while sum(left.values()):
        best = None
        best_score = -1
        for theme_id in THEME_IDS:
            if left[theme_id] == 0:
                continue
            for review in pools[theme_id]:
                if review.review_id in used:
                    continue
                score = sum(1 for key in remaining if remaining[key] > 0 and key in tags_for(review.text))
                if review.rating <= 3:
                    score += 0.1
                if score > best_score:
                    best = (theme_id, review)
                    best_score = score
        if best is None:
            raise RuntimeError("not enough reviews to fill the E1 sample")
        theme_id, review = best
        used.add(review.review_id)
        left[theme_id] -= 1
        for key in tags_for(review.text):
            if key in remaining and remaining[key] > 0:
                remaining[key] -= 1
        picked.append(
            {
                "id": review.review_id,
                "theme": theme_id,
                "rating": review.rating,
                "chars": len(review.text),
                "tags": sorted(tags_for(review.text)),
            }
        )
    if any(remaining.values()):
        raise RuntimeError(f"E1 mix quotas not met: {remaining}")
    return picked


def write_e1(themes: Themes, path: Path | None = None) -> Path:
    path = path or SETS_DIR / "e1_ids.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"seed": SEED, "reviews": select_e1(themes, seed=SEED)}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


__all__ = [
    "EVAL_DIR",
    "RESULTS_DIR",
    "SEED",
    "SETS_DIR",
    "load_advice",
    "load_branches",
    "load_e1",
    "select_e1",
    "tags_for",
    "write_e1",
]
