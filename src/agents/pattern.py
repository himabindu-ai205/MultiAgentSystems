"""Pattern Agent (Architecture section 6.2, Implementation Plan Phase 3).

Job: say whether a complaint is ``one_off``, ``recurring`` or ``rising``, with the count
and the window behind that call.

Steps: classify the theme, narrow to reviews that look like this complaint, count them
in two 7-day windows ending on the dataset's newest review, apply the signal rules.

Rules this module keeps
-----------------------
* Windows end on ``anchor_date`` (the newest review, 2026-07-04), never on today's date.
* The result holds counts, a window, a theme and review IDs. No review text is ever
  returned, put in an error, or logged. Review IDs are 16 hex characters by contract.
* It reads only the review index and the redacted complaint it is given. It never imports
  another agent.
* It needs no model. The theme classifier is rules plus a word-count model trained on the
  labelled reviews. A model can be plugged in through ``ThemeClassifier``, and its answer
  is checked against the five theme IDs before it is used.
"""

from __future__ import annotations

import math
import re
import tomllib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

from config import PatternSettings, load_settings
from constants import THEME_IDS
from data_layer.themes import Themes
from paths import PATTERN_CONFIG_PATH
from schemas import PatternResult, Window

MAX_SHIFT_WEEKS = 52
_MIN_TERM_LENGTH = 4
_TOKEN = re.compile(r"[^\W\d_]{3,}")
_SUFFIXES = ("ions", "ion", "ing", "ers", "er", "ed", "es", "al", "s", "e")


class ThemeClassifier(Protocol):
    def classify(self, question: str) -> str | None:
        """Return one of the five theme IDs, or None."""


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def stem(token: str) -> str:
    """A light stemmer: strips common endings until none applies, keeping 4+ letters."""
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            if token.endswith(suffix) and len(token) - len(suffix) >= _MIN_TERM_LENGTH:
                token = token[: -len(suffix)]
                changed = True
                break
    return token


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _phrase(text: str) -> tuple[str, ...]:
    return tuple(stem(t) for t in tokens(text))


def _contains(stems: Sequence[str], phrase: tuple[str, ...]) -> bool:
    n = len(phrase)
    if n == 0 or n > len(stems):
        return False
    return any(tuple(stems[i : i + n]) == phrase for i in range(len(stems) - n + 1))


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Vocabulary:
    priority: tuple[str, ...]
    min_score: int
    strong: Mapping[str, tuple[tuple[str, ...], ...]]
    weak: Mapping[str, tuple[tuple[str, ...], ...]]
    stopwords: frozenset[str]
    max_terms: int
    min_term_reviews: int
    max_term_share: float
    need: int
    example_ids: int


def load_vocabulary(path: Path | None = None) -> Vocabulary:
    with open(path or PATTERN_CONFIG_PATH, "rb") as handle:
        raw = tomllib.load(handle)

    priority = tuple(raw["classifier"]["priority"])
    scored = [tid for tid in THEME_IDS if tid != "other"]
    if set(priority) != set(scored):
        raise ValueError(f"pattern.toml: priority must list exactly {scored}")
    for theme_id in scored:
        entry = raw["themes"].get(theme_id)
        if not entry or not entry.get("strong"):
            raise ValueError(f"pattern.toml: theme {theme_id} has no strong words")

    narrowing = raw["narrowing"]
    stop = frozenset(raw["narrowing"]["stopwords"])
    return Vocabulary(
        priority=priority,
        min_score=int(raw["classifier"]["min_score"]),
        strong={t: tuple(_phrase(w) for w in raw["themes"][t]["strong"]) for t in scored},
        weak={t: tuple(_phrase(w) for w in raw["themes"][t].get("weak", [])) for t in scored},
        stopwords=stop | frozenset(stem(w) for w in stop),
        max_terms=int(narrowing["max_terms"]),
        min_term_reviews=int(narrowing["min_term_reviews"]),
        max_term_share=float(narrowing["max_term_share"]),
        need=int(narrowing["need"]),
        example_ids=int(narrowing["example_ids"]),
    )


# ---------------------------------------------------------------------------
# Review index, built once from the loaded data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Entry:
    review_id: str
    day: date
    stems: frozenset[str]


class _WordModel:
    """Multinomial word-count model over the labelled reviews. The example-based fallback."""

    def __init__(self, labelled: Sequence[tuple[Sequence[str], str]]) -> None:
        self._counts: dict[str, Counter[str]] = {t: Counter() for t in THEME_IDS}
        self._docs: Counter[str] = Counter()
        for stems, theme_id in labelled:
            self._docs[theme_id] += 1
            self._counts[theme_id].update(stems)
        vocabulary = set().union(*self._counts.values())
        self._vocab_size = max(len(vocabulary), 1)
        self._totals = {t: sum(c.values()) for t, c in self._counts.items()}
        self._n = max(sum(self._docs.values()), 1)

    def predict(self, stems: Sequence[str]) -> str:
        known = [s for s in stems if any(s in c for c in self._counts.values())]
        if not known:
            return "other"
        best_theme, best_score = "other", -math.inf
        for theme_id in THEME_IDS:
            if not self._docs[theme_id]:
                continue
            score = math.log(self._docs[theme_id] / self._n)
            denominator = self._totals[theme_id] + 0.5 * self._vocab_size
            for s in known:
                score += math.log((self._counts[theme_id][s] + 0.5) / denominator)
            if score > best_score:
                best_theme, best_score = theme_id, score
        return best_theme


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


class PatternAgent:
    """Reads only the review index and the redacted complaint it is given."""

    def __init__(
        self,
        themes: Themes,
        *,
        settings: PatternSettings | None = None,
        vocabulary: Vocabulary | None = None,
        classifier: ThemeClassifier | None = None,
    ) -> None:
        self._settings = settings or load_settings().pattern
        self._vocab = vocabulary or load_vocabulary()
        self._classifier = classifier
        self._anchor: date = themes.anchor_date

        self._index: dict[str, list[_Entry]] = {t: [] for t in THEME_IDS}
        self._df: dict[str, Counter[str]] = {t: Counter() for t in THEME_IDS}
        labelled: list[tuple[list[str], str]] = []
        for review_id, review in themes.reviews.items():
            theme_id = themes.review_theme[review_id]
            stems = [stem(t) for t in tokens(review.text)]
            useful = [s for s in stems if s not in self._vocab.stopwords]
            self._index[theme_id].append(_Entry(review_id, review.date.date(), frozenset(stems)))
            self._df[theme_id].update(set(useful))
            labelled.append((useful, theme_id))
        self._model = _WordModel(labelled)

    # -- public ------------------------------------------------------------

    @property
    def anchor_date(self) -> date:
        return self._anchor

    def run(
        self,
        redacted_text: str,
        *,
        theme_hint: str | None = None,
        window_shift_weeks: int = 0,
    ) -> PatternResult:
        """Judge one redacted complaint. Never raises: failures become an ``error`` result.

        ``theme_hint`` skips the classifier. ``window_shift_weeks`` moves both windows back,
        for "the fee issue from last week".
        """
        try:
            return self._run(redacted_text, theme_hint, window_shift_weeks)
        except Exception:
            # No message is kept. It could contain the complaint.
            return PatternResult(status="error", error="PATTERN_INTERNAL_ERROR")

    def classify_theme(self, redacted_text: str) -> str:
        """The theme for a complaint: rules first, then the optional model, then the word model."""
        stems = [stem(t) for t in tokens(redacted_text)]
        ruled = self._rule_theme(stems)
        if ruled:
            return ruled
        if self._classifier is not None:
            try:
                guess = self._classifier.classify(redacted_text)
            except Exception:
                guess = None
            if guess in THEME_IDS:
                return guess  # type: ignore[return-value]
        return self._model.predict([s for s in stems if s not in self._vocab.stopwords])

    def weekly_counts(self, theme_id: str, weeks: int = 12) -> list[int]:
        """Theme-level review counts per 7-day window, newest first. Used for tuning."""
        return [
            self._count(self._index[theme_id], *self._window(self._settings.window_days, k))
            for k in range(weeks)
        ]

    # -- internals ---------------------------------------------------------

    def _run(self, text: str, theme_hint: str | None, shift_weeks: int) -> PatternResult:
        if not isinstance(text, str) or not text.strip():
            return PatternResult(status="error", error="PATTERN_BAD_INPUT")
        if theme_hint is not None and theme_hint not in THEME_IDS:
            return PatternResult(status="error", error="PATTERN_BAD_THEME_HINT")
        if (
            not isinstance(shift_weeks, int)
            or isinstance(shift_weeks, bool)
            or not 0 <= shift_weeks <= MAX_SHIFT_WEEKS
        ):
            return PatternResult(status="error", error="PATTERN_BAD_WINDOW")

        theme_id = theme_hint or self.classify_theme(text)
        entries = self._index[theme_id]

        # Keyword narrowing, with a fall back to the whole theme.
        terms = self._key_terms(text, theme_id)
        matched = self._matching(entries, terms)
        if matched:
            basis, pool = "keyword_match", matched
        else:
            basis, pool = "theme_level", entries

        days = self._settings.window_days
        cur_start, cur_end = self._window(days, shift_weeks)
        prior_start, prior_end = self._window(days, shift_weeks + 1)
        count_current = self._count(pool, cur_start, cur_end)
        count_prior = self._count(pool, prior_start, prior_end)
        weekly = [
            self._count(pool, *self._window(days, shift_weeks + k))
            for k in range(self._settings.baseline_weeks)
        ]
        signal = self._signal(count_current, count_prior, weekly)
        if basis == "theme_level":
            # Nothing in the theme looks like this complaint, so the theme's volume is weak
            # evidence. It may say "recurring" (the theme is busy) but never "rising":
            # a surge in the whole theme must not hold a ticket that has nothing to do with it.
            # "other" is a catch-all, not a topic, so it cannot raise any signal.
            if theme_id == "other":
                signal = "one_off"
            elif signal == "rising":
                signal = "recurring"

        in_window = sorted(
            (e for e in pool if cur_start <= e.day <= cur_end), key=lambda e: e.day, reverse=True
        )
        return PatternResult(
            status="ok",
            theme_id=theme_id,
            signal=signal,
            count_current=count_current,
            count_prior=count_prior,
            window=Window(start=cur_start, end=cur_end, days=days, anchor_date=self._anchor),
            basis=basis,
            matched_review_ids=tuple(e.review_id for e in in_window[: self._vocab.example_ids]),
        )

    def _rule_theme(self, stems: Sequence[str]) -> str | None:
        scores: dict[str, int] = {}
        for theme_id in self._vocab.priority:
            score = 0
            for phrase in self._vocab.strong[theme_id]:
                score += 2 if _contains(stems, phrase) else 0
            for phrase in self._vocab.weak[theme_id]:
                score += 1 if _contains(stems, phrase) else 0
            scores[theme_id] = score
        top = max(scores.values())
        if top < self._vocab.min_score:
            return None
        # max() keeps the first in `priority` order on a tie.
        return max(self._vocab.priority, key=lambda t: scores[t])

    def _key_terms(self, text: str, theme_id: str) -> list[str]:
        total = len(self._index[theme_id]) or 1
        ceiling = self._vocab.max_term_share * total
        seen: dict[str, int] = {}
        for token in tokens(text):
            s = stem(token)
            if len(s) < _MIN_TERM_LENGTH or token in self._vocab.stopwords or s in self._vocab.stopwords:
                continue
            df = self._df[theme_id].get(s, 0)
            if self._vocab.min_term_reviews <= df <= ceiling:
                seen[s] = df
        return [s for s, _ in sorted(seen.items(), key=lambda item: (item[1], item[0]))][
            : self._vocab.max_terms
        ]

    def _matching(self, entries: Sequence[_Entry], terms: Sequence[str]) -> list[_Entry]:
        if not terms:
            return []
        need = min(self._vocab.need, len(terms))
        wanted = set(terms)
        return [e for e in entries if len(wanted & e.stems) >= need]

    def _window(self, days: int, weeks_back: int) -> tuple[date, date]:
        end = self._anchor - timedelta(days=days * weeks_back)
        return end - timedelta(days=days - 1), end

    @staticmethod
    def _count(entries: Sequence[_Entry], start: date, end: date) -> int:
        return sum(1 for e in entries if start <= e.day <= end)

    def _signal(self, current: int, prior: int, weekly: Sequence[int]) -> str:
        cfg = self._settings
        if current >= cfg.min_count and current >= cfg.rising_ratio * prior:
            return "rising"
        present = sum(1 for count in weekly if count > 0)
        if current >= cfg.min_count or present >= 3:
            return "recurring"
        return "one_off"


def run_pattern(
    redacted_text: str,
    themes: Themes,
    *,
    theme_hint: str | None = None,
    window_shift_weeks: int = 0,
) -> PatternResult:
    """Convenience wrapper with the default settings and no model hook."""
    return PatternAgent(themes).run(
        redacted_text, theme_hint=theme_hint, window_shift_weeks=window_shift_weeks
    )
