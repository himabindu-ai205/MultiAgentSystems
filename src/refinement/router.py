"""The refinement router (Implementation Plan Phase 8, Architecture section 10).

It turns a piece of human feedback into a plan: which agents to re-run and with what
parameters. It decides nothing else. The Orchestrator carries the plan out.

Rules first, then a model, then "Orchestrator only":

1. Word rules cover the three documented cases (shorter, a different scheme, a check of the
   pattern over another time window) and a compliance concern.
2. If no rule matches and a classifier is set, the classifier is asked for one label from a
   fixed list. Anything else it says counts as ``unclear``.
3. Ambiguous feedback re-drafts only, and the plan carries a fixed notice that tells the
   human to rephrase.

The classifier chooses the agents and never the parameters. A scheme, a theme and a time
window can only come from the rules, which read them from the human's words, so a model
cannot send an agent anywhere the words do not point.

A plan holds fixed values and one of a few fixed notices. It never holds the feedback text.
The caller redacts the feedback before it gets here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from agents.facts import Vocabulary, load_vocabulary, normalise, resolve_schemes
from constants import FEEDBACK_LABELS, REFINEMENT_CATEGORIES, RefinementCategory, ReplyStyle

MAX_SHIFT_WEEKS = 52

# The graph node each agent runs under. Kept here as plain names so the router stays free of
# the graph's imports. Order matters: a plan lists its nodes in this order.
_NODE_ORDER = ("facts", "pattern", "compliance_inbound")

# Highest priority first. The first trigger that fires names the round.
_PRIORITY = ("scheme_change", "pattern_check", "compliance_concern", "style")
_NODE_OF = {
    "scheme_change": "facts",
    "pattern_check": "pattern",
    "compliance_concern": "compliance_inbound",
}

NOTICE_NO_SCHEME_NAMED = (
    "No scheme was named, so Facts looked again from the complaint. "
    "Name the scheme, for example 'Mid Cap', to switch to it."
)
NOTICE_SEVERAL_SCHEMES = "You named more than one scheme. Please rephrase with one scheme, for example 'Mid Cap'."
NOTICE_OTHER_COMPANY = (
    "Only the five HDFC schemes in the corpus are covered. Please rephrase with one of them."
)
NOTICE_UNCLEAR = (
    "I couldn't tell which agents to re-run, so only the wording was redrafted. Please rephrase, "
    "for example: 'shorter', 'they're asking about Mid Cap', or "
    "'check if this is the fee issue from last week'."
)


class FeedbackClassifier(Protocol):
    def classify(self, text: str) -> str: ...


@dataclass(frozen=True)
class RefinementPlan:
    """What a refinement will do. ``None`` means "leave this as it is"."""

    category: RefinementCategory
    # Graph nodes to run, in graph order. Empty means a re-draft only.
    nodes: tuple[str, ...] = ()
    style: ReplyStyle | None = None
    scheme_override: str | None = None
    theme_hint: str | None = None
    window_shift_weeks: int | None = None
    notice: str | None = None


# ---------------------------------------------------------------------------
# Word rules
# ---------------------------------------------------------------------------


def _any(norm: str, phrases: tuple[str, ...]) -> bool:
    return any(re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", norm) for p in phrases)


_SHORTER = (
    "shorter", "short", "shorten", "shortened", "brief", "briefer", "concise", "trim",
    "tighten", "fewer words", "less wordy", "too long", "too wordy", "cut it down",
)  # fmt: skip

_SCHEME_CUES = (
    "different scheme", "different fund", "another scheme", "another fund", "other scheme",
    "other fund", "wrong scheme", "wrong fund", "not this scheme", "not this fund",
)  # fmt: skip

_COMPLIANCE_CUES = (
    "advice", "advise", "advising", "advisory", "compliance", "compliant", "recommend",
    "recommendation", "recommending", "guarantee", "guaranteed", "promise", "promises",
    "misleading", "sebi",
)  # fmt: skip

_PATTERN_CUES = (
    "issue", "issues", "problem", "problems", "pattern", "trend", "trending", "recurring",
    "rising", "happening", "others", "other users", "other customers", "same", "check", "seen",
)  # fmt: skip

_THEME_CUES: dict[str, tuple[str, ...]] = {
    "fees_brokerage": ("fee", "fees", "brokerage", "charge", "charges", "charged", "deducted", "deduction"),
    "money_movement": (
        "withdraw", "withdrawal", "withdrawals", "payment", "payments", "deposit", "deposits",
        "payout", "refund", "upi", "transfer", "bank",
    ),
    "trading_charts": ("chart", "charts", "order", "orders", "trade", "trades", "trading"),
    "reliability": (
        "crash", "crashes", "crashing", "slow", "lag", "login", "bug", "glitch", "outage",
        "freeze", "freezing",
    ),
}  # fmt: skip

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "couple of": 2, "a": 1,
}  # fmt: skip
_WEEKS_AGO = re.compile(
    r"(?<!\w)(\d+|" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r") weeks? ago(?!\w)"
)
_FIXED_WINDOWS = (
    "last week", "previous week", "prior week", "week before", "previous 7 days",
    "last month", "a month ago", "previous month", "month before",
)  # fmt: skip


def _window_weeks(norm: str) -> int | None:
    match = _WEEKS_AGO.search(norm)
    if match:
        raw = match.group(1)
        weeks = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
        return min(weeks, MAX_SHIFT_WEEKS) if weeks > 0 else None
    for phrase in _FIXED_WINDOWS:
        if re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", norm):
            return 4 if "month" in phrase else 1
    return None


def _theme(norm: str) -> str | None:
    """The theme whose cue word comes first in the feedback."""
    first: tuple[int, str] | None = None
    for theme_id, words in _THEME_CUES.items():
        for word in words:
            found = re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", norm)
            if found and (first is None or found.start() < first[0]):
                first = (found.start(), theme_id)
    return first[1] if first else None


# ---------------------------------------------------------------------------
# The router
# ---------------------------------------------------------------------------


class RefinementRouter:
    def __init__(
        self,
        *,
        classifier: FeedbackClassifier | None = None,
        vocabulary: Vocabulary | None = None,
    ) -> None:
        self._classifier = classifier
        self._vocab = vocabulary or load_vocabulary()

    def route(self, redacted_feedback: str) -> RefinementPlan:
        """The plan for one piece of (already redacted) feedback. Never raises."""
        text = redacted_feedback if isinstance(redacted_feedback, str) else ""
        norm = normalise(text)

        scheme, scheme_notice = self._read_scheme(norm)
        theme, window = _theme(norm), _window_weeks(norm)

        triggers = self._rule_triggers(norm, scheme, theme, window, scheme_notice)
        if not triggers and norm:
            triggers = self._classified_trigger(text)

        if not triggers:
            return RefinementPlan(category="unclear", notice=scheme_notice or NOTICE_UNCLEAR)

        category = next(t for t in _PRIORITY if t in triggers)
        nodes = tuple(n for n in _NODE_ORDER if any(_NODE_OF.get(t) == n for t in triggers))
        notice = scheme_notice
        if "scheme_change" in triggers and scheme is None and notice is None:
            notice = NOTICE_NO_SCHEME_NAMED
        return RefinementPlan(
            category=category,  # type: ignore[arg-type]
            nodes=nodes,
            style="shorter" if "style" in triggers else None,
            scheme_override=scheme if "scheme_change" in triggers else None,
            theme_hint=theme if "pattern_check" in triggers else None,
            window_shift_weeks=window if "pattern_check" in triggers else None,
            notice=notice,
        )

    # -- internals ---------------------------------------------------------

    def _read_scheme(self, norm: str) -> tuple[str | None, str | None]:
        """The one scheme the words name, and a notice if they name something unusable."""
        resolution = resolve_schemes(norm, self._vocab)
        if resolution.outsider:
            return None, NOTICE_OTHER_COMPANY
        if len(resolution.schemes) > 1:
            return None, NOTICE_SEVERAL_SCHEMES
        return (resolution.schemes[0] if resolution.schemes else None), None

    def _rule_triggers(
        self,
        norm: str,
        scheme: str | None,
        theme: str | None,
        window: int | None,
        scheme_notice: str | None,
    ) -> set[str]:
        triggers: set[str] = set()
        if scheme_notice is None and (scheme is not None or _any(norm, _SCHEME_CUES)):
            triggers.add("scheme_change")
        if window is not None or (theme is not None and _any(norm, _PATTERN_CUES)):
            triggers.add("pattern_check")
        if _any(norm, _COMPLIANCE_CUES):
            triggers.add("compliance_concern")
        if _any(norm, _SHORTER):
            triggers.add("style")
        return triggers

    def _classified_trigger(self, text: str) -> set[str]:
        if self._classifier is None:
            return set()
        try:
            label = self._classifier.classify(text)
        except Exception:  # noqa: BLE001 - nothing from the failure is kept
            return set()
        if not isinstance(label, str) or label not in FEEDBACK_LABELS:
            return set()
        if label == "shorter":
            return {"style"}
        if label in REFINEMENT_CATEGORIES and label != "unclear":
            return {label}
        return set()


__all__ = [
    "FEEDBACK_LABELS",
    "FeedbackClassifier",
    "MAX_SHIFT_WEEKS",
    "RefinementPlan",
    "RefinementRouter",
]
