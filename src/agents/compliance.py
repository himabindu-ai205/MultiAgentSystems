"""Compliance Agent (Architecture section 6.3, Implementation Plan Phase 4).

Job: flag advice language, performance or return claims, and PII. It has two modes that
share one set of rules.

* ``inbound``  reads the redacted complaint and finds users *asking* for advice, an
  opinion on a scheme, or a return figure (``advice_request``), plus any PII left in it.
* ``outbound`` reads a draft reply and finds the reply *giving* advice
  (``advice_in_text``), making a performance or return claim (``performance_claim``),
  or carrying PII (``pii_residual``). The Reviewer calls this mode.

Rules this module keeps
-----------------------
* **Stateless.** Every call is judged on its own, so rephrasing or asking again later
  cannot get round a refusal. The only memory is the ``advice_attempts`` counter, which
  lives in the session. ``is_advice_request`` tells the session when to count one.
* **Flags carry codes, never text.** A flag's ``detail`` is a rule code such as
  ``rule.should_i_verb``. The matched words are not kept.
* **Refusals pass.** Outbound rules look for advice *constructions* ("you should sell",
  "we recommend"), not for the bare verbs, so "we can't advise whether to buy or sell"
  is clean.
* **Stricter wins.** If the rules and the optional model disagree, the flag stays.
  If the model fails, the result is an ``error`` and never ``clean``.
* It never imports another agent, and it needs no model: the rule layer works alone.
"""

from __future__ import annotations

import re
import tomllib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from constants import PII_TYPES
from data_layer.corpus import Corpus
from ingress.redactor import find_pii
from paths import COMPLIANCE_CONFIG_PATH
from schemas import ComplianceResult, Flag, PiiCounts
from textutil import split_sentences

LABELS = ("advice_request", "advice_in_text", "performance_claim", "none")

_VERBS_TOKEN = "<VERBS>"
_CONTEXT_TOKENS = {"<CONTEXT_INVEST_PLUS>": "invest_plus", "<CONTEXT_INVEST>": "invest"}
_PERCENT = re.compile(r"\d+(?:\.\d+)?\s*(?:%|percent|per\s+cent)")
_PERCENT_LIST = re.compile(r"\d\s*(?:%|percent)\s*(?:,|and|or|to|-)\s*$")


class AdviceClassifier(Protocol):
    """Optional model layer. Catches paraphrases the phrase lists miss."""

    def classify(self, text: str, *, mode: str) -> str:
        """Return one of ``advice_request``, ``advice_in_text``, ``performance_claim``, ``none``."""


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    flag_type: str
    code: str
    pattern: re.Pattern[str]
    requires: re.Pattern[str] | None = None

    def fires(self, text: str) -> bool:
        if self.requires is not None and not self.requires.search(text):
            return False
        return self.pattern.search(text) is not None


@dataclass(frozen=True)
class RuleSet:
    inbound: tuple[Rule, ...]
    outbound: tuple[Rule, ...]
    return_words: re.Pattern[str]
    fee_context: re.Pattern[str]


def _compile(source: str, verbs: str, context: dict[str, str]) -> re.Pattern[str]:
    source = source.replace(_VERBS_TOKEN, verbs)
    for token, key in _CONTEXT_TOKENS.items():
        source = source.replace(token, context[key])
    return re.compile(source, re.IGNORECASE)


def load_rules(path: Path | None = None) -> RuleSet:
    with open(path or COMPLIANCE_CONFIG_PATH, "rb") as handle:
        raw = tomllib.load(handle)

    verbs = raw["verbs"]["list"]
    context = raw["context"]

    def build(entries: Sequence[dict], default_type: str | None) -> tuple[Rule, ...]:
        rules = []
        for entry in entries:
            code = entry["code"]
            if not re.fullmatch(r"[a-z0-9_.:-]{1,64}", code):
                raise ValueError(f"compliance.toml: bad rule code {code!r}")
            requires = entry.get("requires")
            rules.append(
                Rule(
                    flag_type=entry.get("type", default_type or ""),
                    code=code,
                    pattern=_compile(entry["pattern"], verbs, context),
                    requires=_compile(requires, verbs, context) if requires else None,
                )
            )
        return tuple(rules)

    inbound = build(raw.get("inbound", []), "advice_request")
    outbound = build(raw.get("outbound", []), None)
    if not inbound or not outbound:
        raise ValueError("compliance.toml: both inbound and outbound rules are required")
    if any(r.flag_type not in {"advice_in_text", "performance_claim"} for r in outbound):
        raise ValueError("compliance.toml: outbound rules must be advice_in_text or performance_claim")

    percent = raw["percent"]
    return RuleSet(
        inbound=inbound,
        outbound=outbound,
        return_words=re.compile(percent["return_words"], re.IGNORECASE),
        fee_context=re.compile(percent["fee_context"], re.IGNORECASE),
    )


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _clean(text: str) -> str:
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def _squash(text: str) -> str:
    return " ".join(re.sub(r"[^\w%]+", " ", text.lower()).split())


def tax_sentences(corpus: Corpus) -> frozenset[str]:
    """The corpus's own tax and stamp-duty sentences, normalised, for the whitelist."""
    found: set[str] = set()
    for scheme in corpus.schemes.values():
        block = scheme.sections.get("Stamp duty and tax", "")
        for sentence in re.split(r"(?<=[A-Za-z0-9%)])\.\s+(?=[A-Z])", block):
            squashed = _squash(sentence)
            if squashed:
                found.add(squashed)
    return frozenset(found)


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


class ComplianceAgent:
    """Stateless. Reads only the text it is given."""

    def __init__(
        self,
        *,
        rules: RuleSet | None = None,
        corpus: Corpus | None = None,
        classifier: AdviceClassifier | None = None,
    ) -> None:
        self._rules = rules or load_rules()
        self._whitelist = tax_sentences(corpus) if corpus is not None else frozenset()
        self._classifier = classifier

    # -- public ------------------------------------------------------------

    def run(self, text: str, *, mode: str = "inbound") -> ComplianceResult:
        """Check one text. Never raises: failures become an ``error`` result."""
        safe_mode = mode if mode in ("inbound", "outbound") else "inbound"
        try:
            if mode not in ("inbound", "outbound"):
                return _error(safe_mode, "COMPLIANCE_BAD_MODE")
            if not isinstance(text, str) or not text.strip():
                return _error(safe_mode, "COMPLIANCE_BAD_INPUT")
            return self._run(text, safe_mode)
        except _ClassifierFailure:
            return _error(safe_mode, "COMPLIANCE_CLASSIFIER_FAILED")
        except Exception:
            # No message is kept. It could contain the text.
            return _error(safe_mode, "COMPLIANCE_INTERNAL_ERROR")

    def inbound(self, redacted_complaint: str) -> ComplianceResult:
        return self.run(redacted_complaint, mode="inbound")

    def outbound(self, draft: str) -> ComplianceResult:
        return self.run(draft, mode="outbound")

    # -- internals ---------------------------------------------------------

    def _run(self, text: str, mode: str) -> ComplianceResult:
        cleaned = _clean(text)
        lowered = cleaned.lower()
        flags: list[Flag] = []

        if mode == "inbound":
            for rule in self._rules.inbound:
                if rule.fires(lowered):
                    flags.append(Flag(type="advice_request", detail=rule.code))
        else:
            flags.extend(self._outbound_flags(cleaned))

        flags.extend(self._model_flags(cleaned, mode))

        counts = Counter(match.type for match in find_pii(text))
        for pii_type in PII_TYPES:
            if counts[pii_type]:
                flags.append(Flag(type="pii_residual", detail=f"pii.{pii_type.lower()}"))

        unique = tuple(dict.fromkeys(flags))
        return ComplianceResult(
            mode=mode,
            status="ok",
            flags=unique,
            pii_redacted=PiiCounts(**{t: counts[t] for t in PII_TYPES}),
            clean=not unique,
        )

    def _outbound_flags(self, text: str) -> list[Flag]:
        flags: list[Flag] = []
        for sentence in split_sentences(text):
            lowered = sentence.lower()
            allowed_tax = bool(self._whitelist) and any(w in _squash(sentence) for w in self._whitelist)
            for rule in self._rules.outbound:
                if rule.flag_type == "performance_claim" and allowed_tax:
                    continue
                if rule.fires(lowered):
                    flags.append(Flag(type=rule.flag_type, detail=rule.code))  # type: ignore[arg-type]
            if not allowed_tax and self._percent_claim(lowered):
                flags.append(Flag(type="performance_claim", detail="rule.percent_returns"))
        return flags

    def _percent_claim(self, sentence: str) -> bool:
        """A percentage that sits next to a return word and is not a tax or fee figure."""
        previous_exempt = False
        for match in _PERCENT.finditer(sentence):
            before = sentence[max(0, match.start() - 45) : match.start()]
            exempt = bool(self._rules.fee_context.search(before)) or (
                previous_exempt and bool(_PERCENT_LIST.search(before))
            )
            previous_exempt = exempt
            if exempt:
                continue
            window = sentence[max(0, match.start() - 45) : match.end() + 45]
            if self._rules.return_words.search(window):
                return True
        return False

    def _model_flags(self, text: str, mode: str) -> list[Flag]:
        if self._classifier is None:
            return []
        try:
            label = self._classifier.classify(text, mode=mode)
        except Exception:
            raise _ClassifierFailure from None
        if label not in LABELS:
            raise _ClassifierFailure
        if label == "none":
            return []
        if mode == "inbound":
            return [Flag(type="advice_request", detail="classifier.advice")] if label == "advice_request" else []
        flag_type = "performance_claim" if label == "performance_claim" else "advice_in_text"
        return [Flag(type=flag_type, detail=f"classifier.{label}")]  # type: ignore[arg-type]


class _ClassifierFailure(Exception):
    """The optional model failed or gave an unusable label. Carries no text."""


def _error(mode: str, code: str) -> ComplianceResult:
    return ComplianceResult(mode=mode, status="error", clean=False, error=code)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helpers for the Orchestrator and the session
# ---------------------------------------------------------------------------


def is_advice_request(result: ComplianceResult) -> bool:
    """True when the session should add one to ``advice_attempts``."""
    return result.status == "ok" and any(f.type == "advice_request" for f in result.flags)


def has_pii(result: ComplianceResult) -> bool:
    return any(f.type == "pii_residual" for f in result.flags)


def hard_fail_types(result: ComplianceResult) -> tuple[str, ...]:
    """The flag types the Reviewer treats as a hard fail on a draft."""
    return tuple(dict.fromkeys(f.type for f in result.flags))


def run_compliance(text: str, mode: str = "inbound", corpus: Corpus | None = None) -> ComplianceResult:
    """Convenience wrapper with the default rules and no model."""
    return ComplianceAgent(corpus=corpus).run(text, mode=mode)


__all__ = [
    "AdviceClassifier",
    "ComplianceAgent",
    "RuleSet",
    "has_pii",
    "hard_fail_types",
    "is_advice_request",
    "load_rules",
    "run_compliance",
    "tax_sentences",
]
