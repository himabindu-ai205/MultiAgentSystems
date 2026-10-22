"""Stub agents with fixed, valid results.

They let later phases be built and tested before the real agents exist. Each stub
takes the redacted complaint, ignores it, and returns a canned result chosen by
``scenario``. Every scenario is a valid contract object, so the decision table, the
drafter and the Reviewer can be exercised on all the cases they must handle.

None of these stubs reads the data files or calls a model.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from constants import SCHEME_SOURCE_URLS
from schemas import (
    ComplianceResult,
    FactsResult,
    Flag,
    PatternResult,
    Searched,
    Window,
)

FactsScenario = Literal["covered", "not_covered", "ambiguous", "error"]
PatternScenario = Literal["one_off", "recurring", "rising", "error"]
ComplianceScenario = Literal["clean", "advice_request", "error"]

# Fixed values taken from the real corpus.
_FETCHED_AT = "2026-06-28T06:28:54+00:00"
_ANCHOR = date(2026, 7, 4)
_SAMPLE_REVIEW_IDS = ("a7424c3b2558887b", "a0a9a46a0e263ab6", "400c49abe8397efd")


def stub_facts(redacted_text: str = "", *, scenario: FactsScenario = "covered") -> FactsResult:
    if scenario == "covered":
        return FactsResult(
            status="covered",
            answer="HDFC Large Cap Fund Direct Growth has an exit load of 1% if redeemed within 1 year.",
            citation=SCHEME_SOURCE_URLS["large_cap"],
            scheme_id="large_cap",
            section_title="Exit load",
            searched=Searched(schemes=("large_cap",), sections=("Exit load",)),
            source_fetched_at=_FETCHED_AT,
        )
    if scenario == "not_covered":
        return FactsResult(
            status="not_covered",
            searched=Searched(
                schemes=("large_cap", "mid_cap", "small_cap", "gold_fof", "silver_fof"),
                sections=("Key fund facts", "Exit load"),
            ),
        )
    if scenario == "ambiguous":
        return FactsResult(status="ambiguous", candidates=("large_cap", "gold_fof"))
    return FactsResult(status="error", error="STUB_FACTS_ERROR")


def stub_pattern(redacted_text: str = "", *, scenario: PatternScenario = "one_off") -> PatternResult:
    if scenario == "error":
        return PatternResult(status="error", error="STUB_PATTERN_ERROR")
    counts = {
        "one_off": (1, 0),
        "recurring": (8, 7),
        "rising": (40, 10),
    }[scenario]
    return PatternResult(
        status="ok",
        theme_id="fees_brokerage",
        signal=scenario,
        count_current=counts[0],
        count_prior=counts[1],
        window=Window(start=date(2026, 6, 28), end=_ANCHOR, days=7, anchor_date=_ANCHOR),
        basis="keyword_match",
        matched_review_ids=_SAMPLE_REVIEW_IDS,
    )


def stub_compliance(
    redacted_text: str = "",
    *,
    scenario: ComplianceScenario = "clean",
    mode: Literal["inbound", "outbound"] = "inbound",
) -> ComplianceResult:
    if scenario == "clean":
        return ComplianceResult(mode=mode, status="ok", clean=True)
    if scenario == "advice_request":
        return ComplianceResult(
            mode=mode,
            status="ok",
            clean=False,
            flags=(Flag(type="advice_request", detail="rule.should_i"),),
        )
    return ComplianceResult(mode=mode, status="error", clean=False, error="STUB_COMPLIANCE_ERROR")
