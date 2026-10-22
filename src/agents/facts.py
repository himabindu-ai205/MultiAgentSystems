"""Facts Agent (Architecture section 6.1, Implementation Plan Phase 2).

Job: answer from the scheme corpus with exactly one citation, or say it can't.

Steps: resolve the scheme, route to one of the seven sections, look the block up
directly, copy the answer from that block only, cite the scheme's ``source_url``.

The model only classifies
-------------------------
This agent's one model call is an optional ``SectionClassifier``. It is used for
questions the keywords miss, and it can only return one of the seven section titles
or nothing. Anything else is ignored and the deterministic result stands.

No model writes the answer. Code copies the matching sentence or sentences from the
retrieved block, so every word and number in ``answer`` comes from the scheme file.
The one exception is a fixed template for fund managers ("is managed by", "and"),
because that block is long, noisy free text that must not be copied whole. A final
check rejects any answer that contains a number the block does not contain.

What it never does
------------------
* It never uses general knowledge. A block is the only source of an answer.
* It never imports another agent. It reads only the redacted complaint it is given.
* It never puts the question text into its result, its error or a log line.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol

from constants import SCHEME_IDS, SECTION_TITLES
from data_layer.corpus import Corpus, Scheme
from paths import FACTS_CONFIG_PATH
from schemas import FactsResult, Searched

# ---------------------------------------------------------------------------
# The model hook, optional. The model only classifies, it never writes the answer.
# ---------------------------------------------------------------------------


class SectionClassifier(Protocol):
    def classify(self, question: str) -> str | None:
        """Return one of the seven section titles, or None."""


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


def normalise(text: str) -> str:
    """Lower case, punctuation turned into spaces, spaces collapsed."""
    return " ".join(re.sub(r"[^\w%]+", " ", text.lower()).split())


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)" + re.escape(normalise(phrase)) + r"(?!\w)")


@dataclass(frozen=True)
class KeyFactTopic:
    keywords: tuple[str, ...]
    labels: tuple[str, ...]


@dataclass(frozen=True)
class Vocabulary:
    aliases: Mapping[str, tuple[str, ...]]
    section_keywords: Mapping[str, tuple[str, ...]]
    key_facts: tuple[KeyFactTopic, ...]
    out_of_scope: tuple[str, ...]
    other_amcs: tuple[str, ...]
    amc_context_words: tuple[str, ...]
    other_funds: tuple[str, ...]
    weak_keywords: frozenset[str]
    context_words: tuple[str, ...]


def load_vocabulary(path: Path | None = None) -> Vocabulary:
    with open(path or FACTS_CONFIG_PATH, "rb") as handle:
        raw = tomllib.load(handle)

    aliases = {sid: tuple(normalise(a) for a in raw["schemes"][sid]["aliases"]) for sid in SCHEME_IDS}
    sections = {title: tuple(normalise(k) for k in raw["sections"].get(title, [])) for title in SECTION_TITLES}
    topics = tuple(
        KeyFactTopic(tuple(normalise(k) for k in t["keywords"]), tuple(t["labels"]))
        for t in raw["key_facts"]
    )
    # Key fund facts is reached by its own keywords and by any item keyword.
    sections["Key fund facts"] = tuple(
        dict.fromkeys(sections["Key fund facts"] + tuple(k for t in topics for k in t.keywords))
    )

    missing = [title for title, words in sections.items() if not words]
    if missing:
        raise ValueError(f"facts.toml: no keywords for section(s): {missing}")
    unknown = raw["unknown"]
    return Vocabulary(
        aliases=aliases,
        section_keywords=sections,
        key_facts=topics,
        out_of_scope=tuple(normalise(p) for p in raw["out_of_scope"]["phrases"]),
        other_amcs=tuple(normalise(p) for p in unknown["amcs"]),
        amc_context_words=tuple(normalise(p) for p in unknown["amc_context_words"]),
        other_funds=tuple(normalise(p) for p in unknown["funds"]),
        weak_keywords=frozenset(normalise(p) for p in raw["ambiguity"]["weak_keywords"]),
        context_words=tuple(normalise(p) for p in raw["ambiguity"]["context_words"]),
    )


# ---------------------------------------------------------------------------
# Scheme resolver
# ---------------------------------------------------------------------------

_FUZZY_MIN_RATIO = 0.86
_FUZZY_MIN_LENGTH = 6


@dataclass(frozen=True)
class Resolution:
    schemes: tuple[str, ...] = ()
    # The question names something that is not one of the five schemes.
    outsider: bool = False


def _spans(norm: str, phrases: Sequence[str]) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    for phrase in phrases:
        found.extend(m.span() for m in _phrase_pattern(phrase).finditer(norm))
    return found


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _other_amc_named(norm: str, vocab: Vocabulary) -> bool:
    tokens_after = lambda end: norm[end:].split()[:4]  # noqa: E731
    for _, end in _spans(norm, vocab.other_amcs):
        if any(word in vocab.amc_context_words for word in tokens_after(end)):
            return True
    return False


def _fuzzy_scheme(norm: str, vocab: Vocabulary) -> str | None:
    tokens = norm.split()
    best: tuple[float, str] = (0.0, "")
    for scheme_id, aliases in vocab.aliases.items():
        for alias in aliases:
            if len(alias) < _FUZZY_MIN_LENGTH:
                continue
            width = len(alias.split())
            for i in range(len(tokens) - width + 1):
                ratio = SequenceMatcher(None, alias, " ".join(tokens[i : i + width])).ratio()
                if ratio > best[0]:
                    best = (ratio, scheme_id)
    return best[1] if best[0] >= _FUZZY_MIN_RATIO else None


def resolve_schemes(norm: str, vocab: Vocabulary) -> Resolution:
    """Which of the five schemes does the question name?"""
    if _other_amc_named(norm, vocab):
        return Resolution(outsider=True)

    unknown_spans = _spans(norm, vocab.other_funds)
    hits: list[tuple[int, str]] = []
    for scheme_id, aliases in vocab.aliases.items():
        for span in _spans(norm, aliases):
            if any(_overlap(span, other) for other in unknown_spans):
                continue  # "large and mid cap" must not count as "mid cap"
            hits.append((span[0], scheme_id))
    if hits:
        ordered = tuple(dict.fromkeys(sid for _, sid in sorted(hits)))
        return Resolution(schemes=ordered)
    if unknown_spans:
        return Resolution(outsider=True)

    guess = _fuzzy_scheme(norm, vocab)
    return Resolution(schemes=(guess,)) if guess else Resolution()


# ---------------------------------------------------------------------------
# Section router
# ---------------------------------------------------------------------------


def route_section(norm: str, vocab: Vocabulary) -> tuple[str, str] | None:
    """``(section, keyword)`` for the keyword that appears first in the question, or None."""
    best: tuple[int, int, str, str] | None = None
    for order, title in enumerate(SECTION_TITLES):
        for phrase in vocab.section_keywords[title]:
            match = _phrase_pattern(phrase).search(norm)
            if match and (best is None or (match.start(), order) < best[:2]):
                best = (match.start(), order, title, phrase)
    return (best[2], best[3]) if best else None


def looks_like_a_fund_question(norm: str, keyword: str, vocab: Vocabulary) -> bool:
    """With no scheme named, is this clearly about a mutual fund, so asking "which one?" makes sense?

    A brokerage-tax or "product manager" remark also contains section keywords. Those are
    weak keywords, and they only count when a fund word is present too.
    """
    return keyword not in vocab.weak_keywords or bool(_spans(norm, vocab.context_words))


def mentions_out_of_scope(norm: str, vocab: Vocabulary) -> bool:
    return bool(_spans(norm, vocab.out_of_scope))


# ---------------------------------------------------------------------------
# Extraction, always from the retrieved block only
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[A-Za-z0-9%)])\.\s+(?=[A-Z])")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_MANAGER = re.compile(
    r"\b[A-Z]{1,3} ((?:[A-Z][a-z]+ ){1,3}?)(\w{3} \d{4} - (?:Present|\w{3} \d{4})) View details"
)
_MIN_PART = re.compile(r"Min\. for [^₹]*?₹[\d,]+")


def _sentences(text: str) -> list[str]:
    return [s.strip().rstrip(".").strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]


def _join(scheme: Scheme, parts: Sequence[str]) -> str:
    return f"{scheme.scheme_name}: " + ". ".join(p.rstrip(".") for p in parts) + "."


def numbers_in(text: str) -> set[str]:
    return {token.rstrip(",.") for token in _NUMBER.findall(text)}


def unsupported_numbers(answer: str, block: str) -> list[str]:
    """Numbers in ``answer`` that do not appear in ``block``. An empty list means supported."""
    allowed = numbers_in(block)
    return sorted(n for n in numbers_in(answer) if n not in allowed)


def _extract_key_facts(scheme: Scheme, block: str, norm: str, vocab: Vocabulary) -> str | None:
    items = _sentences(block)[1:]  # the first sentence is the block title
    by_label = {item.split(":", 1)[0].strip(): item for item in items if ":" in item}
    wanted: list[str] = []
    for topic in vocab.key_facts:
        if any(_phrase_pattern(k).search(norm) for k in topic.keywords):
            wanted.extend(label for label in topic.labels if label in by_label)
    chosen = [by_label[label] for label in dict.fromkeys(wanted)] or list(by_label.values())
    return _join(scheme, chosen) if chosen else None


def _extract_tax(scheme: Scheme, block: str, norm: str) -> str | None:
    sentences = _sentences(block)
    stamp = [s for s in sentences if s.lower().startswith("stamp duty")]
    tax = [s for s in sentences if not s.lower().startswith("stamp duty")]
    asks_stamp = "stamp duty" in norm
    asks_tax = bool(re.search(r"(?<!\w)(tax|taxes|taxed|taxation|capital gains?|ltcg|stcg)(?!\w)", norm))
    chosen = stamp if asks_stamp and not asks_tax else tax if asks_tax and not asks_stamp else sentences
    return _join(scheme, chosen or sentences)


def _extract_minimum(scheme: Scheme, block: str, norm: str) -> str:
    parts = _MIN_PART.findall(block)
    if not parts:
        return _join(scheme, [block])
    wanted: list[str] = []
    for part in parts:
        low = part.lower()
        if ("sip" in norm.split() and "sip" in low) or (
            re.search(r"lump|1st|first", norm) and "1st" in low
        ) or (re.search(r"2nd|second", norm) and "2nd" in low):
            wanted.append(part)
    return _join(scheme, wanted or parts)


def _extract_management(scheme: Scheme, block: str) -> str | None:
    managers = [f"{m.group(1).strip()} ({m.group(2)})" for m in _MANAGER.finditer(block)]
    if not managers:
        return None
    if len(managers) == 1:
        names = managers[0]
    else:
        names = ", ".join(managers[:-1]) + " and " + managers[-1]
    return f"{scheme.scheme_name} is managed by {names}."


def extract_answer(scheme: Scheme, section_title: str, norm: str, vocab: Vocabulary) -> str | None:
    """The deterministic answer: a restatement of the block, or part of it."""
    block = scheme.sections[section_title]
    if section_title == "Key fund facts":
        return _extract_key_facts(scheme, block, norm, vocab)
    if section_title == "Stamp duty and tax":
        return _extract_tax(scheme, block, norm)
    if section_title == "Minimum investments":
        return _extract_minimum(scheme, block, norm)
    if section_title == "Fund management":
        return _extract_management(scheme, block)
    return _join(scheme, [block])


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------

_ALL_SCHEMES = tuple(SCHEME_IDS)
_ALL_SECTIONS = tuple(SECTION_TITLES)


class FactsAgent:
    """Reads only the scheme corpus and the redacted complaint it is given."""

    def __init__(
        self,
        corpus: Corpus,
        *,
        vocabulary: Vocabulary | None = None,
        classifier: SectionClassifier | None = None,
    ) -> None:
        self._corpus = corpus
        self._vocab = vocabulary or load_vocabulary()
        self._classifier = classifier

    # -- public ------------------------------------------------------------

    def run(self, redacted_text: str, *, scheme_override: str | None = None) -> FactsResult:
        """Answer one redacted complaint. Never raises: failures become an ``error`` result."""
        try:
            return self._run(redacted_text, scheme_override)
        except Exception:
            # No message is kept. It could contain the complaint.
            return FactsResult(status="error", error="FACTS_INTERNAL_ERROR")

    # -- internals ---------------------------------------------------------

    def _run(self, text: str, scheme_override: str | None) -> FactsResult:
        if not isinstance(text, str) or not text.strip():
            return FactsResult(status="error", error="FACTS_BAD_INPUT")
        norm = normalise(text)

        # 1. Which scheme?
        if scheme_override is not None:
            if scheme_override not in SCHEME_IDS:
                return FactsResult(status="error", error="FACTS_BAD_SCHEME_OVERRIDE")
            resolution = Resolution(schemes=(scheme_override,))
        else:
            resolution = resolve_schemes(norm, self._vocab)

        # 2. Which section?
        routed = route_section(norm, self._vocab)
        section = routed[0] if routed else None
        keyword = routed[1] if routed else ""
        if section is None and self._classifier is not None:
            section = self._classify(text)
            keyword = ""  # a classifier only fires on a fund question, so treat it as strong

        searched_schemes = resolution.schemes or _ALL_SCHEMES
        searched_sections = (section,) if section else _ALL_SECTIONS

        # Returns, performance and ranking are not in the corpus. Answering with a
        # different fact (the benchmark, say) would look like an answer, so refuse.
        # A tax question may say "returns" ("how are returns taxed"), so it is exempt.
        if (
            section is not None
            and section != "Stamp duty and tax"
            and mentions_out_of_scope(norm, self._vocab)
        ):
            return self._not_covered(searched_schemes, _ALL_SECTIONS)

        if resolution.outsider and not resolution.schemes:
            return self._not_covered(searched_schemes, searched_sections)
        if section is None:
            # Returns, performance, ranking, app problems, or anything else the corpus lacks.
            return self._not_covered(searched_schemes, searched_sections)
        if len(resolution.schemes) == 0:
            if not looks_like_a_fund_question(norm, keyword, self._vocab):
                return self._not_covered(_ALL_SCHEMES, _ALL_SECTIONS)
            return FactsResult(status="ambiguous", candidates=_ALL_SCHEMES)
        if len(resolution.schemes) > 1:
            return FactsResult(status="ambiguous", candidates=resolution.schemes)

        # 3. Retrieve, 4. extract, 5. cite.
        scheme = self._corpus.get(resolution.schemes[0])
        block = scheme.sections[section]
        answer = self._answer(text, norm, scheme, section, block)
        if answer is None:
            return self._not_covered((scheme.scheme_id,), (section,))
        return FactsResult(
            status="covered",
            answer=answer,
            citation=scheme.source_url,
            scheme_id=scheme.scheme_id,
            section_title=section,
            searched=Searched(schemes=(scheme.scheme_id,), sections=(section,)),
            source_fetched_at=scheme.fetched_at,
        )

    def _classify(self, text: str) -> str | None:
        try:
            guess = self._classifier.classify(text) if self._classifier else None
        except Exception:
            return None
        return guess if guess in SECTION_TITLES else None

    def _answer(self, text: str, norm: str, scheme: Scheme, section: str, block: str) -> str | None:
        answer = extract_answer(scheme, section, norm, self._vocab)
        if answer and unsupported_numbers(answer, block):
            return None  # never ship a number the block does not contain
        return answer

    @staticmethod
    def _not_covered(schemes: Sequence[str], sections: Sequence[str]) -> FactsResult:
        return FactsResult(
            status="not_covered",
            searched=Searched(schemes=tuple(schemes), sections=tuple(sections)),
        )


def run_facts(redacted_text: str, corpus: Corpus, *, scheme_override: str | None = None) -> FactsResult:
    """Convenience wrapper with the default vocabulary and no model."""
    return FactsAgent(corpus).run(redacted_text, scheme_override=scheme_override)
