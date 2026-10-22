"""The reply drafter (Architecture section 8.2, Implementation Plan Phase 5).

The wording of a reply is built from constrained templates. A model may be plugged in
to fill in the wording (``DraftWriter``), but it never gets the complaint and it never
decides anything:

* The drafter takes the disposition as an input and cannot change it.
* The only facts a reply may carry are the sentences of ``FactsResult.answer``.
* The only link is ``FactsResult.citation``, and the date is ``source_fetched_at``.
* The model's text is accepted only if it passes every check below. If it fails or
  raises, the template text is used instead, so a draft is never worse than the template.
* A model is never sent the complaint. It gets a ``DraftRequest`` that holds only
  corpus text, scheme names, section names and the style. The drafter does not even
  accept the complaint as an argument.

Replies are at most three sentences, counted the same way the Reviewer counts them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from agents.compliance import ComplianceAgent
from constants import MAX_REPLY_SENTENCES, REPLY_STYLES, SCHEME_IDS, SECTION_TITLES
from data_layer.corpus import Corpus
from schemas import ComplianceResult, DispositionValue, FactsResult, ReplyDraft
from textutil import count_sentences, format_last_checked, numbers_in, split_sentences, urls_in

# ---------------------------------------------------------------------------
# Data tables
# ---------------------------------------------------------------------------

SCHEME_LABELS: dict[str, str] = {
    "large_cap": "HDFC Large Cap Fund",
    "mid_cap": "HDFC Mid Cap Fund",
    "small_cap": "HDFC Small Cap Fund",
    "gold_fof": "HDFC Gold ETF Fund of Fund",
    "silver_fof": "HDFC Silver ETF FoF",
}

REFUSAL_TEXT = "I can't give investment advice, say whether to buy, sell or hold, or predict returns."
NOT_COVERED_OPENING = (
    "I can't answer this from the HDFC scheme information I have, and I don't want to guess."
)
NOT_COVERED_CLOSING = "If you share the fund name and what you want to know, we can check again."
AMBIGUOUS_CLOSING = "Once you tell us, we can share its details from the scheme page."
ANSWER_CLOSING = "If this doesn't match what you see, reply and we will look into it."

Wording = Literal["template", "model", "template_fallback"]

# Reason codes. Fixed text, so they can be logged.
BAD_INPUT = "DRAFT_BAD_INPUT"
BAD_DATE = "DRAFT_BAD_FETCHED_AT"
TEMPLATE_FLAGGED = "DRAFT_FLAGGED"
WRITER_FAILED = "WRITER_FAILED"
WRITER_BAD_OUTPUT = "WRITER_BAD_OUTPUT"


# ---------------------------------------------------------------------------
# The optional model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DraftRequest:
    """Everything a model is allowed to see. It holds no complaint text."""

    kind: str
    style: str
    max_sentences: int
    # The statements the reply may restate. Empty when the reply holds no fact.
    facts: tuple[str, ...]
    citation: str | None
    last_checked: str | None
    scheme_names: tuple[str, ...]
    section_titles: tuple[str, ...]
    # The deterministic wording. A model may rephrase it but must keep what it says.
    template: str
    # On the one rework round: the Reviewer's named failure. Fixed phrases and counts,
    # never a quote from the rejected draft.
    rework: str | None = None


class DraftWriter(Protocol):
    """Optional wording step. Return the reply text."""

    def write(self, request: DraftRequest) -> str: ...


def build_prompt(request: DraftRequest) -> str:
    """A ready-made prompt for a ``DraftWriter`` that calls a language model."""
    lines = [
        "Rewrite the reply below for a customer-support message about a mutual fund.",
        f"Use at most {request.max_sentences} sentences.",
        "Keep every fact, number and name exactly as given. Add no other fact, number or link.",
        "Do not give advice, opinions, forecasts or return figures.",
    ]
    if request.style == "shorter":
        lines.append("Make it as short as you can.")
    if request.citation:
        lines.append(f"Include this link exactly once: {request.citation}")
        lines.append(f"Include this date: {request.last_checked}")
    else:
        lines.append("Do not include any link.")
    if request.rework:
        lines.append(f"A reviewer rejected the previous version. Fix this: {request.rework}")
    lines.append("")
    lines.append("Reply to rewrite:")
    lines.append(request.template)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DraftOutcome:
    """The draft, or ``None`` when no safe draft could be made."""

    draft: ReplyDraft | None
    wording: Wording = "template"
    # Why the model text was not used, or why no draft was made. A fixed code, never text.
    reason_code: str | None = None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _end(sentence: str) -> str:
    sentence = sentence.strip()
    return sentence if sentence[-1:] in ".!?" else sentence + "."


def pack_answer(answer: str, budget: int) -> list[str]:
    """Return the answer as at most ``budget`` sentences without dropping anything.

    A long answer such as the key-facts list is joined into one sentence with
    semicolons. Every word and number is kept.
    """
    sentences = split_sentences(answer)
    if len(sentences) <= budget:
        return [_end(s) for s in sentences]
    return [_end("; ".join(s.strip().rstrip(".!?; ") for s in sentences))]


def _names(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " or " + items[-1]


def _scheme_phrase(scheme_ids: tuple[str, ...]) -> str:
    if not scheme_ids or set(scheme_ids) >= set(SCHEME_IDS):
        return "all five HDFC scheme pages"
    return _names([SCHEME_LABELS.get(s, s) for s in scheme_ids])


def _section_phrase(titles: tuple[str, ...]) -> str:
    if not titles or set(titles) >= set(SECTION_TITLES):
        return "every section"
    return "the " + _names(list(titles)) + " section" + ("s" if len(titles) > 1 else "")


def _source_sentence(citation: str, last_checked: str) -> str:
    return f"Source: {citation} (last checked {last_checked})."


# ---------------------------------------------------------------------------
# The drafter
# ---------------------------------------------------------------------------


class Drafter:
    """Builds the reply for a disposition. Stateless."""

    def __init__(
        self,
        *,
        writer: DraftWriter | None = None,
        corpus: Corpus | None = None,
        outbound_check: Callable[[str], ComplianceResult] | None = None,
        max_sentences: int = MAX_REPLY_SENTENCES,
    ) -> None:
        if max_sentences < 2:
            raise ValueError("max_sentences must be at least 2, to leave room for the source line")
        self._writer = writer
        self._check = outbound_check or ComplianceAgent(corpus=corpus).outbound
        self._max = max_sentences

    # -- public ------------------------------------------------------------

    def draft(
        self,
        disposition: DispositionValue,
        facts: FactsResult | None,
        *,
        style: str = "standard",
        rework: str | None = None,
    ) -> DraftOutcome:
        """Draft the reply for ``disposition``. Never raises and never changes it.

        ``rework`` is the Reviewer's named failure, on the one rework round. It reaches the
        model, if there is one, and nothing else: the template is the same either way.
        """
        if style not in REPLY_STYLES or disposition == "HOLD_AGENT_FAILURE" or facts is None:
            return DraftOutcome(None, reason_code=BAD_INPUT)
        note = rework.strip() if isinstance(rework, str) and rework.strip() else None
        try:
            return self._draft(disposition, facts, style, note)
        except Exception:
            # No message is kept. It could contain text.
            return DraftOutcome(None, reason_code=BAD_INPUT)

    # -- internals ---------------------------------------------------------

    def _draft(
        self, disposition: DispositionValue, facts: FactsResult, style: str, rework: str | None
    ) -> DraftOutcome:
        covered = facts.status == "covered"
        if disposition in ("SEND_READY", "HOLD_DISAGREEMENT") and not covered:
            return DraftOutcome(None, reason_code=BAD_INPUT)
        if disposition == "NOT_COVERED" and facts.status not in ("not_covered", "ambiguous"):
            return DraftOutcome(None, reason_code=BAD_INPUT)

        last_checked = None
        if covered:
            last_checked = format_last_checked(facts.source_fetched_at)
            # A refusal can leave the fact out. An answer cannot go without its date.
            if last_checked is None and disposition in ("SEND_READY", "HOLD_DISAGREEMENT"):
                return DraftOutcome(None, reason_code=BAD_DATE)

        request = replace(self._request(disposition, facts, style, last_checked), rework=rework)
        template = request.template

        if self._flagged(template):
            return DraftOutcome(None, reason_code=TEMPLATE_FLAGGED)

        kind = request.kind
        citation = request.citation
        chosen, wording, reason = template, "template", None
        if self._writer is not None:
            text, reason = self._ask_writer(request)
            if text is not None:
                chosen, wording = text, "model"
            else:
                wording = "template_fallback"

        draft = ReplyDraft(
            text=chosen,
            citation=citation,
            last_checked=last_checked if citation else None,
            kind=kind,  # type: ignore[arg-type]
        )
        return DraftOutcome(draft, wording=wording, reason_code=reason)  # type: ignore[arg-type]

    def _request(
        self,
        disposition: DispositionValue,
        facts: FactsResult,
        style: str,
        last_checked: str | None,
    ) -> DraftRequest:
        shorter = style == "shorter"
        limit = min(self._max, 2) if shorter else self._max
        covered = facts.status == "covered"
        fact_sentences: tuple[str, ...] = ()
        citation: str | None = None
        names: tuple[str, ...] = ()
        titles: tuple[str, ...] = ()

        if disposition in ("SEND_READY", "HOLD_DISAGREEMENT"):
            kind = "answer" if disposition == "SEND_READY" else "escalation_hold"
            citation = facts.citation
            assert facts.answer is not None and citation is not None and last_checked is not None
            body = pack_answer(facts.answer, limit - 1)
            fact_sentences = tuple(body)
            sentences = [*body, _source_sentence(citation, last_checked)]
            if not shorter and len(sentences) < limit:
                sentences.append(ANSWER_CLOSING)
            names = (SCHEME_LABELS.get(facts.scheme_id or "", ""),) if facts.scheme_id else ()
            titles = (facts.section_title,) if facts.section_title else ()

        elif disposition == "REFUSAL":
            kind = "refusal"
            sentences = [REFUSAL_TEXT]
            # A refusal may restate a fact only when Facts found one for a fact question.
            if covered and not shorter and facts.answer and facts.citation and last_checked:
                body = pack_answer(facts.answer, limit - 2)
                sentences = [REFUSAL_TEXT, *body, _source_sentence(facts.citation, last_checked)]
                fact_sentences = tuple(body)
                citation = facts.citation
                names = (SCHEME_LABELS.get(facts.scheme_id or "", ""),) if facts.scheme_id else ()
                titles = (facts.section_title,) if facts.section_title else ()

        else:  # NOT_COVERED
            kind = "not_covered"
            if facts.status == "ambiguous":
                options = [SCHEME_LABELS.get(c, c) for c in facts.candidates]
                names = tuple(options)
                sentences = [f"Which scheme do you mean: {_names(options)}?"]
                if not shorter:
                    sentences.append(AMBIGUOUS_CLOSING)
            else:
                names = tuple(SCHEME_LABELS.get(s, s) for s in facts.searched.schemes)
                titles = tuple(facts.searched.sections)
                # What was searched is always named (Architecture 8.2), even when shorter.
                sentences = [
                    NOT_COVERED_OPENING,
                    f"I looked at {_section_phrase(titles)} in "
                    f"{_scheme_phrase(tuple(facts.searched.schemes))}.",
                ]
                if not shorter:
                    sentences.append(NOT_COVERED_CLOSING)

        text = " ".join(sentences[:limit])
        return DraftRequest(
            kind=kind,
            style=style,
            max_sentences=limit,
            facts=fact_sentences,
            citation=citation,
            last_checked=last_checked if citation else None,
            scheme_names=tuple(n for n in names if n),
            section_titles=titles,
            template=text,
        )

    def _flagged(self, text: str) -> bool:
        result = self._check(text)
        return not (result.status == "ok" and result.clean)

    def _ask_writer(self, request: DraftRequest) -> tuple[str | None, str | None]:
        assert self._writer is not None
        try:
            text = self._writer.write(request)
        except Exception:
            return None, WRITER_FAILED
        if not self._acceptable(text, request):
            return None, WRITER_BAD_OUTPUT
        return text.strip(), None

    def _acceptable(self, text: object, request: DraftRequest) -> bool:
        """Every rule a model's wording must satisfy. Anything else uses the template."""
        if not isinstance(text, str) or not text.strip():
            return False
        text = text.strip()
        if count_sentences(text) > request.max_sentences:
            return False

        found_links = urls_in(text)
        if request.citation is None:
            if found_links:
                return False
        else:
            if found_links != [request.citation]:
                return False
            if request.last_checked is None or request.last_checked not in text:
                return False

        # No number may appear that the corpus did not give, and none may go missing.
        allowed_text = " ".join(
            [*request.facts, request.last_checked or "", *request.scheme_names, *request.section_titles]
        )
        allowed = numbers_in(allowed_text)
        given = numbers_in(text)
        if not given <= allowed:
            return False
        if not numbers_in(" ".join(request.facts)) <= given:
            return False

        return not self._flagged(text)


__all__ = [
    "Drafter",
    "DraftOutcome",
    "DraftRequest",
    "DraftWriter",
    "SCHEME_LABELS",
    "build_prompt",
    "format_last_checked",
    "pack_answer",
]
