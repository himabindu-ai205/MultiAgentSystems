"""The Reviewer gate (Architecture section 9, Implementation Plan Phase 7).

It sits between the Orchestrator and the human. It combines deterministic checks with one
outbound call to the Compliance Agent. It never edits a draft. It passes it, or fails it
with named reasons.

Rules this module keeps
-----------------------
* **Deterministic first.** Every check except the outbound Compliance call is plain code.
  The Compliance call may use a model for advice wording, and only ever makes the result
  stricter.
* **Fail closed.** A blank draft, an error from the outbound call, or an exception from it
  is a fail, never a pass (edge cases ORC-15 and REV-17).
* **Names, never text.** A failure is a code plus a short instruction built from fixed
  phrases and counts. It never quotes the draft, so a rework message cannot carry PII or
  a stray claim back into the Orchestrator or a log.
* **It only judges.** The result has no field for replacement text.
* **One rework round.** ``round`` is 1 for the first look at a draft and 2 for the look
  after the single rework. A second failure is final, and the draft is flagged.

What counts as a factual claim
------------------------------
A sentence is a claim unless it is one of the drafter's fixed sentences, a bare source
line, or a question without a number. Of the rest, a sentence is a claim if it has a digit
or a currency sign, uses scheme-fact vocabulary (exit load, benchmark, minimum, managed by
and so on), or repeats several words from ``FactsResult.answer``. A draft with a claim
needs exactly one link, equal to ``FactsResult.citation``, and the source date. This is a
deliberately strict test. A false fail costs one rework round. A missed claim ships an
uncited fact.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from agents.compliance import ComplianceAgent
from constants import MAX_REPLY_SENTENCES, MAX_REVIEWER_REWORK_ROUNDS
from data_layer.corpus import Corpus
from ingress.redactor import find_pii
from logging_allowlist import AllowListLogger
from schemas import ComplianceResult, FactsResult, ReplyDraft, ReviewerResult, ReviewHandoff
from textutil import count_sentences, format_last_checked, split_sentences, urls_in

OutboundCheck = Callable[[str], ComplianceResult]

# ---------------------------------------------------------------------------
# What is and is not a factual claim
# ---------------------------------------------------------------------------

# The drafter's fixed sentences. The drafter and the Reviewer are separate on purpose, so
# the Reviewer keeps its own copy. A test fails if the two drift apart.
_FIXED_SENTENCES = frozenset(
    {
        "i can't give investment advice, say whether to buy, sell or hold, or predict returns",
        "i can't answer this from the hdfc scheme information i have, and i don't want to guess",
        "if you share the fund name and what you want to know, we can check again",
        "once you tell us, we can share its details from the scheme page",
        "if this doesn't match what you see, reply and we will look into it",
    }
)

# "I looked at the Exit load section in HDFC Large Cap Fund." Letters only, so a number or
# a fact cannot be tucked inside.
_SEARCHED = re.compile(
    r"^i looked at (?:the [a-z ,]+? sections?|every section) in [a-z ,]+\.?$", re.IGNORECASE
)

_FACT_TERMS = re.compile(
    r"\b(?:exit load|expense ratio|benchmark|minimum|sip|lock-?in|riskometer|nav|aum|fund size|"
    r"fund manager|managed by|manages|stamp duty|taxed?|taxation|objective|redeem(?:ed)?|units?)\b",
    re.IGNORECASE,
)
_NUMBER_OR_MONEY = re.compile(r"[\d\u20b9%]")
_URL_WORDS = re.compile(r"https?://\S+", re.IGNORECASE)
_DATE = re.compile(r"\b\d{1,2}-[A-Za-z]{3}-\d{4}\b")
_CITATION_WORDS = re.compile(r"\b(?:sources?|last\s+checked|checked|as\s+of|link)\b", re.IGNORECASE)
_WORDS = re.compile(r"[a-z]{4,}")
_BARE_LINK = re.compile(
    r"(?<![\w/.@-])(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:in|com|org|net|io|co|app|gov)\b"
    r"(?:/[^\s)\]>\"']*)?",
    re.IGNORECASE,
)

# Words too common to show that a sentence repeats the fact.
_COMMON = frozenset(
    {"that", "this", "with", "from", "have", "your", "will", "within", "which", "their", "there"}
)


def _normal(sentence: str) -> str:
    cleaned = sentence.replace("\u2019", "'").replace("\u2018", "'")
    return re.sub(r"\s+", " ", cleaned).strip().rstrip(".!? ").lower()


def _is_citation_line(sentence: str) -> bool:
    """A bare source line: nothing left once links, dates and 'source' wording are removed."""
    rest = _URL_WORDS.sub(" ", sentence)
    rest = _DATE.sub(" ", rest)
    rest = _CITATION_WORDS.sub(" ", rest)
    return not re.search(r"[A-Za-z0-9]", rest)


def _content_words(text: str) -> set[str]:
    return {w for w in _WORDS.findall(text.lower()) if w not in _COMMON}


def _is_claim(sentence: str, answer_words: set[str]) -> bool:
    if _normal(sentence) in _FIXED_SENTENCES or _SEARCHED.match(sentence.strip()):
        return False
    if _is_citation_line(sentence):
        return False
    if _NUMBER_OR_MONEY.search(sentence):
        return True
    if sentence.rstrip().endswith("?"):
        return False
    if _FACT_TERMS.search(sentence):
        return True
    return len(_content_words(sentence) & answer_words) >= 3


def _links(text: str) -> list[str]:
    """Every link in the text. A link without ``https://`` counts, so it cannot hide."""
    found = urls_in(text)
    remainder = text
    for url in found:
        remainder = remainder.replace(url, " ")
    found.extend(m.group().rstrip(".,;:!?") for m in _BARE_LINK.finditer(remainder))
    return found


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


def _instructions(draft_sentences: int, claim_at: int | None, limit: int) -> dict[str, str]:
    where = f"Sentence {claim_at}" if claim_at else "The reply"
    return {
        "EMPTY_DRAFT": "Write a reply. The draft was empty.",
        "UNCITED_CLAIM": (
            f"{where} states a fact without the source link. "
            "Give the one source link, or remove the claim."
        ),
        "CITATION_MISMATCH": (
            "Use exactly one link, the one given as the source, and no other link."
        ),
        "MISSING_LAST_CHECKED": "Add the last-checked date next to the source link.",
        "TOO_LONG": f"Use at most {limit} sentences. The reply has {draft_sentences}.",
        "ADVICE_LANGUAGE": (
            "Remove advice, recommendations, forward-looking wording "
            "and return or performance figures."
        ),
        "PII_SURVIVED": "Remove all personal data, such as IDs, phone numbers, emails or account numbers.",
        "COMPLIANCE_CHECK_FAILED": (
            "The compliance check could not finish. Keep the reply short, factual and cited."
        ),
    }


_CODE_ORDER = (
    "EMPTY_DRAFT",
    "UNCITED_CLAIM",
    "CITATION_MISMATCH",
    "MISSING_LAST_CHECKED",
    "TOO_LONG",
    "ADVICE_LANGUAGE",
    "PII_SURVIVED",
    "COMPLIANCE_CHECK_FAILED",
)


# ---------------------------------------------------------------------------
# The Reviewer
# ---------------------------------------------------------------------------


class Reviewer:
    """Pass a draft or fail it with named reasons. Stateless, and never edits."""

    def __init__(
        self,
        outbound_check: OutboundCheck | None = None,
        *,
        corpus: Corpus | None = None,
        logger: AllowListLogger | None = None,
        max_sentences: int = MAX_REPLY_SENTENCES,
    ) -> None:
        self._check_outbound: OutboundCheck = outbound_check or ComplianceAgent(corpus=corpus).outbound
        self._logger = logger
        self._max = max_sentences

    def __call__(self, handoff: ReviewHandoff) -> ReviewerResult:
        return self.review(handoff)

    def review(self, handoff: ReviewHandoff) -> ReviewerResult:
        codes, claim_at, sentence_count = self._failures(handoff.draft, handoff.facts)
        failed = [c for c in _CODE_ORDER if c in codes]

        if not failed:
            result = ReviewerResult(verdict="PASS", round=handoff.round)
        else:
            text = _instructions(sentence_count, claim_at, self._max)
            result = ReviewerResult(
                verdict="FAIL",
                round=handoff.round,
                failure_types=tuple(failed),  # type: ignore[arg-type]
                instruction=" ".join(text[c] for c in failed),
                # The look after the one rework is final.
                flagged=handoff.round > MAX_REVIEWER_REWORK_ROUNDS,
            )

        if self._logger is not None:
            self._logger.log(
                "reviewer",
                request_id=handoff.request_id,
                round=result.round,
                verdict=result.verdict,
                failure_types=result.failure_types,
            )
        return result

    # -- internals ---------------------------------------------------------

    def _failures(self, draft: ReplyDraft, facts: FactsResult | None) -> tuple[set[str], int | None, int]:
        text = draft.text
        if not text.strip():
            return {"EMPTY_DRAFT"}, None, 0

        codes: set[str] = set()
        sentences = split_sentences(text)
        count = count_sentences(text)

        # Facts the draft is allowed to rest on. Only a covered result has a citation.
        covered = facts is not None and facts.status == "covered"
        citation = facts.citation if covered else None
        answer_words = _content_words(facts.answer or "") if covered else set()

        claim_at = next(
            (i for i, s in enumerate(sentences, start=1) if _is_claim(s, answer_words)), None
        )
        has_claim = claim_at is not None or draft.kind in ("answer", "escalation_hold")

        # Citation: exactly one link, and it must be the one Facts returned.
        links = _links(text)
        if links:
            if citation is None or links != [citation]:
                codes.add("CITATION_MISMATCH")
        if draft.citation is not None and draft.citation != citation:
            codes.add("CITATION_MISMATCH")
        if has_claim and (not links or citation is None):
            codes.add("UNCITED_CLAIM")

        # Last-checked date: required whenever there is a claim.
        if has_claim:
            expected = format_last_checked(facts.source_fetched_at) if covered else None
            if expected is None or expected not in text or draft.last_checked != expected:
                codes.add("MISSING_LAST_CHECKED")

        if count > self._max:
            codes.add("TOO_LONG")

        # Advice, performance claims and PII: the outbound Compliance call, backed by the
        # Reviewer's own PII detectors.
        codes |= self._compliance_codes(text)
        if find_pii(text):
            codes.add("PII_SURVIVED")

        return codes, claim_at or (1 if "UNCITED_CLAIM" in codes else None), count

    def _compliance_codes(self, text: str) -> set[str]:
        try:
            result = self._check_outbound(text)
        except Exception:
            # No message is kept. It could contain the draft.
            return {"COMPLIANCE_CHECK_FAILED"}
        if not isinstance(result, ComplianceResult) or result.status != "ok":
            return {"COMPLIANCE_CHECK_FAILED"}

        codes: set[str] = set()
        for flag in result.flags:
            if flag.type == "pii_residual":
                codes.add("PII_SURVIVED")
            else:  # advice_in_text, performance_claim, and anything unexpected
                codes.add("ADVICE_LANGUAGE")
        return codes


__all__ = ["OutboundCheck", "Reviewer"]
