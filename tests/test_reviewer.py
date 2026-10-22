"""Phase 7: the Reviewer gate (Architecture section 9, edge cases REV-01 to REV-21)."""

from __future__ import annotations

import pytest

from agents.stubs import stub_compliance, stub_facts
from constants import SCHEME_SOURCE_URLS
from logging_allowlist import AllowListLogger
from orchestrator.drafter import Drafter
from reviewer.gate import Reviewer
from schemas import (
    ComplianceResult,
    Flag,
    ReplyDraft,
    ReviewHandoff,
    RoutingNote,
    Signals,
    new_request_id,
)
from textutil import count_sentences

URL = SCHEME_SOURCE_URLS["large_cap"]
OTHER_URL = SCHEME_SOURCE_URLS["mid_cap"]
LAST = "28-Jun-2026"  # the date of the stub's fetched_at
FACT = "HDFC Large Cap Fund Direct Growth has an exit load of 1% if redeemed within 1 year."
SOURCE = f"Source: {URL} (last checked {LAST})."
GOOD = f"{FACT} {SOURCE}"

NOTE = RoutingNote(
    team="Mutual Funds Support",
    priority="P3",
    reason="one off",
    signals=Signals(facts="covered", pattern="one_off", compliance="clean"),
)
COVERED = stub_facts(scenario="covered")
NOT_COVERED = stub_facts(scenario="not_covered")


def make(
    text: str,
    *,
    kind: str = "answer",
    facts=COVERED,
    citation: str | None = URL,
    last_checked: str | None = LAST,
    round: int = 1,
) -> ReviewHandoff:
    draft = ReplyDraft(text=text, kind=kind, citation=citation, last_checked=last_checked)
    return ReviewHandoff(
        request_id=new_request_id(), draft=draft, routing_note=NOTE, facts=facts, round=round
    )


def uncovered(text: str, **kw) -> ReviewHandoff:
    kw.setdefault("kind", "not_covered")
    return make(text, facts=NOT_COVERED, citation=None, last_checked=None, **kw)


@pytest.fixture(scope="module")
def reviewer(data):
    return Reviewer(corpus=data.corpus)


def failures(result) -> set[str]:
    return set(result.failure_types)


# ---------------------------------------------------------------------------
# A clean draft
# ---------------------------------------------------------------------------


def test_a_clean_cited_draft_passes_on_the_first_round(reviewer):
    result = reviewer(make(GOOD))
    assert result.verdict == "PASS" and result.round == 1
    assert result.failure_types == () and result.flagged is False and result.instruction is None


def test_a_clean_draft_passes_every_time(reviewer):
    # REV-20: the false-fail rate on a clean draft is zero.
    assert all(reviewer(make(GOOD)).verdict == "PASS" for _ in range(25))


def test_a_not_covered_draft_with_no_claim_and_no_link_passes(reviewer):
    # REV-11
    text = (
        "I can't answer this from the HDFC scheme information I have, and I don't want to guess. "
        "I looked at the Key fund facts or Exit load sections in all five HDFC scheme pages. "
        "If you share the fund name and what you want to know, we can check again."
    )
    assert reviewer(uncovered(text)).verdict == "PASS"


def test_a_refusal_with_no_claim_passes(reviewer):
    text = "I can't give investment advice, say whether to buy, sell or hold, or predict returns."
    assert reviewer(uncovered(text, kind="refusal")).verdict == "PASS"


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------


def test_a_fact_with_no_link_is_an_uncited_claim_and_the_sentence_is_named(reviewer):
    result = reviewer(make(f"{FACT} (last checked {LAST})."))
    assert failures(result) == {"UNCITED_CLAIM"}
    assert "Sentence 1" in result.instruction


def test_the_sentence_number_points_at_the_uncited_sentence(reviewer):
    text = (
        "Thanks for your patience. "
        "HDFC Large Cap Fund Direct Growth has an exit load of 1% if redeemed within 1 year."
    )
    result = reviewer(make(text, citation=None, last_checked=None))
    assert "UNCITED_CLAIM" in failures(result)
    assert "Sentence 2" in result.instruction


def test_an_answer_with_no_link_fails_even_if_its_wording_looks_harmless(reviewer):
    result = reviewer(make("Here is what we found for you.", citation=None, last_checked=None))
    assert "UNCITED_CLAIM" in failures(result)


def test_a_link_outside_the_five_corpus_urls_is_a_mismatch(reviewer):
    text = f"{FACT} Source: https://example.com/exit-load (last checked {LAST})."
    assert failures(reviewer(make(text))) == {"CITATION_MISMATCH"}


def test_a_valid_corpus_link_for_another_scheme_is_a_mismatch(reviewer):
    text = f"{FACT} Source: {OTHER_URL} (last checked {LAST})."
    assert failures(reviewer(make(text))) == {"CITATION_MISMATCH"}


def test_two_links_in_one_reply_fail(reviewer):
    text = f"{FACT} Source: {URL} and {URL} (last checked {LAST})."
    assert failures(reviewer(make(text))) == {"CITATION_MISMATCH"}


def test_a_link_for_the_right_scheme_plus_another_corpus_link_fails(reviewer):
    text = f"{FACT} See {URL} or {OTHER_URL} (last checked {LAST})."
    assert failures(reviewer(make(text))) == {"CITATION_MISMATCH"}


@pytest.mark.parametrize(
    "text",
    [
        f"{FACT} Source: {URL}. Last checked {LAST}.",  # trailing full stop
        f"{FACT} Source: ({URL}) (last checked {LAST}).",  # in brackets
        f"{FACT} Source: [HDFC Large Cap]({URL}) (last checked {LAST}).",  # markdown
        f"{FACT} Source: {URL}, last checked {LAST}.",  # trailing comma
    ],
)
def test_a_correct_link_is_found_whatever_surrounds_it(reviewer, text):
    # REV-05: punctuation around the link is not a mismatch.
    assert reviewer(make(text)).verdict == "PASS", text


def test_a_link_without_a_scheme_is_not_the_citation(reviewer):
    bare = URL.removeprefix("https://")
    text = f"{FACT} Source: {bare} (last checked {LAST})."
    assert "CITATION_MISMATCH" in failures(reviewer(make(text)))


def test_a_link_in_a_reply_that_has_no_citation_is_a_mismatch(reviewer):
    # ORC-20: a link from the complaint is never repeated.
    text = (
        "I can't answer this from the HDFC scheme information I have, and I don't want to guess. "
        "See https://example.com/help."
    )
    assert "CITATION_MISMATCH" in failures(reviewer(uncovered(text)))


def test_the_draft_field_for_the_citation_must_agree_with_the_facts(reviewer):
    result = reviewer(make(GOOD, citation=OTHER_URL))
    assert "CITATION_MISMATCH" in failures(result)


def test_a_fact_in_a_not_covered_draft_fails(reviewer):
    # REV-12: stating a fact "to be helpful" is a claim with no citation.
    text = (
        "I can't answer this from the HDFC scheme information I have, and I don't want to guess. "
        "The exit load is usually 1%."
    )
    result = reviewer(uncovered(text))
    assert "UNCITED_CLAIM" in failures(result)
    assert "Sentence 2" in result.instruction


def test_a_fact_with_no_number_in_a_not_covered_draft_also_fails(reviewer):
    text = (
        "I can't answer this from the HDFC scheme information I have, and I don't want to guess. "
        "The fund is managed by a very experienced team."
    )
    assert "UNCITED_CLAIM" in failures(reviewer(uncovered(text)))


# ---------------------------------------------------------------------------
# Last checked
# ---------------------------------------------------------------------------


def test_a_fact_with_no_last_checked_date_fails(reviewer):
    result = reviewer(make(f"{FACT} Source: {URL}.", last_checked=None))
    assert failures(result) == {"MISSING_LAST_CHECKED"}


def test_a_date_that_is_not_the_source_date_fails(reviewer):
    text = f"{FACT} Source: {URL} (last checked 01-Jan-2020)."
    assert failures(reviewer(make(text, last_checked="01-Jan-2020"))) == {"MISSING_LAST_CHECKED"}


def test_a_draft_that_names_its_date_in_a_field_but_not_in_the_text_fails(reviewer):
    assert failures(reviewer(make(f"{FACT} Source: {URL}."))) == {"MISSING_LAST_CHECKED"}


# ---------------------------------------------------------------------------
# Length
# ---------------------------------------------------------------------------


def test_four_sentences_is_too_long(reviewer):
    text = f"{FACT} It may differ by plan. Please check the page. {SOURCE}"
    result = reviewer(make(text))
    assert failures(result) == {"TOO_LONG"}
    assert "3" in result.instruction and "4" in result.instruction


@pytest.mark.parametrize(
    "text",
    [
        f"The minimum SIP is Rs. 100. The expense ratio is 1.04%. {SOURCE}",
        f"The fee is e.g. 1.04% of the amount. Cr. 5 is not a sentence end. {SOURCE}",
        f"Mr. Chirag Setalvad manages the fund. Rs. 500 is the minimum lump sum. {SOURCE}",
    ],
)
def test_abbreviations_decimals_and_links_do_not_end_a_sentence(reviewer, text):
    # REV-09
    assert count_sentences(text) == 3, text
    assert "TOO_LONG" not in failures(reviewer(make(text, citation=URL)))


# ---------------------------------------------------------------------------
# Advice and PII (outbound Compliance)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        "You may want to stay invested for the long term.",
        "We recommend you buy more units.",
        "This fund has given 15% returns every year.",
    ],
)
def test_advice_or_a_performance_claim_is_a_hard_fail(reviewer, extra):
    result = reviewer(make(f"{FACT} {extra} {SOURCE}"))
    assert "ADVICE_LANGUAGE" in failures(result)


def test_pii_in_a_draft_is_a_hard_fail_and_the_value_is_never_repeated(reviewer):
    secret = "9876543210"
    result = reviewer(make(f"{GOOD} Call {secret}."))
    assert "PII_SURVIVED" in failures(result)
    assert secret not in repr(result) and secret not in (result.instruction or "")


@pytest.mark.parametrize("value", ["ABCDE1234F", "someone@example.com"])
def test_other_pii_types_are_caught_too(reviewer, value):
    assert "PII_SURVIVED" in failures(reviewer(make(f"{GOOD} Ref {value}.")))


def test_pii_is_caught_even_if_the_outbound_check_says_clean(data):
    # The Reviewer's own detectors back up the Compliance call.
    reviewer = Reviewer(outbound_check=lambda text: stub_compliance(mode="outbound"))
    assert "PII_SURVIVED" in failures(reviewer(make(f"{GOOD} Call 9876543210.")))


def test_the_outbound_flags_are_mapped_to_failure_names():
    def check(text: str) -> ComplianceResult:
        return ComplianceResult(
            mode="outbound",
            status="ok",
            clean=False,
            flags=(Flag(type="advice_in_text", detail="rule.you_should"),),
        )

    assert failures(Reviewer(outbound_check=check)(make(GOOD))) == {"ADVICE_LANGUAGE"}


# ---------------------------------------------------------------------------
# Failing closed
# ---------------------------------------------------------------------------


def test_an_outbound_compliance_error_is_never_a_pass():
    # REV-17
    def check(text: str) -> ComplianceResult:
        return ComplianceResult(mode="outbound", status="error", clean=False, error="COMPLIANCE_CLASSIFIER_FAILED")

    result = Reviewer(outbound_check=check)(make(GOOD))
    assert result.verdict == "FAIL"
    assert failures(result) == {"COMPLIANCE_CHECK_FAILED"}


def test_an_outbound_compliance_exception_is_never_a_pass_and_keeps_no_message():
    def check(text: str) -> ComplianceResult:
        raise RuntimeError("secret detail 9876543210")

    result = Reviewer(outbound_check=check)(make(GOOD))
    assert failures(result) == {"COMPLIANCE_CHECK_FAILED"}
    assert "9876543210" not in repr(result)


@pytest.mark.parametrize("text", [" ", "\n\t "])
def test_a_blank_draft_is_never_a_pass(reviewer, text):
    # ORC-15
    result = reviewer(uncovered(text))
    assert result.verdict == "FAIL" and failures(result) == {"EMPTY_DRAFT"}


def test_the_outbound_check_gets_exactly_the_draft_text_once():
    seen: list[str] = []

    def check(text: str) -> ComplianceResult:
        seen.append(text)
        return stub_compliance(mode="outbound")

    Reviewer(outbound_check=check)(make(GOOD))
    assert seen == [GOOD]


def test_every_failure_is_named_not_only_the_first(reviewer):
    text = f"{FACT} You should buy more units. It differs. Check often."
    got = failures(reviewer(make(text, citation=None, last_checked=None)))
    assert {"UNCITED_CLAIM", "ADVICE_LANGUAGE", "TOO_LONG"} <= got


# ---------------------------------------------------------------------------
# The retry message
# ---------------------------------------------------------------------------


def test_the_retry_message_never_quotes_the_draft(reviewer):
    marker = "ZEBRAMARKER"
    text = f"{FACT} You should buy {marker} units now. It differs. Check often."
    result = reviewer(make(text, citation=None, last_checked=None))
    assert result.instruction
    assert marker not in result.instruction
    for piece in (FACT[:30], "buy", "units now"):
        assert piece not in result.instruction


def test_the_retry_message_names_each_failure(reviewer):
    text = f"{FACT} You should buy more units. It differs. Check often."
    result = reviewer(make(text, citation=None, last_checked=None))
    lowered = result.instruction.lower()
    assert "source link" in lowered
    assert "advice" in lowered
    assert "at most 3 sentences" in lowered


# ---------------------------------------------------------------------------
# Rounds
# ---------------------------------------------------------------------------


def test_a_first_failure_is_not_flagged_and_asks_for_one_rework(reviewer):
    result = reviewer(make(f"{FACT} Source: {OTHER_URL} (last checked {LAST}).", round=1))
    assert result.verdict == "FAIL" and result.round == 1
    assert result.flagged is False and result.instruction


def test_a_second_failure_is_flagged(reviewer):
    result = reviewer(make(f"{FACT} Source: {OTHER_URL} (last checked {LAST}).", round=2))
    assert result.verdict == "FAIL" and result.round == 2 and result.flagged is True


def test_a_pass_on_the_second_round_is_not_flagged(reviewer):
    result = reviewer(make(GOOD, round=2))
    assert result.verdict == "PASS" and result.round == 2 and result.flagged is False


def test_a_different_second_failure_is_still_final(reviewer):
    # REV-15: whichever failure comes second ends it.
    result = reviewer(make(f"{FACT} You should buy more units. {SOURCE}", round=2))
    assert result.flagged is True and "ADVICE_LANGUAGE" in failures(result)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def test_each_review_is_logged_with_round_verdict_and_failure_types(data):
    records: list[dict] = []
    logger = AllowListLogger(sink=records.append)
    reviewer = Reviewer(corpus=data.corpus, logger=logger)
    handoff = make(f"{FACT} Source: {OTHER_URL} (last checked {LAST}).")

    reviewer(handoff)
    reviewer(make(GOOD))

    assert [(r["event"], r["round"], r["verdict"]) for r in records] == [
        ("reviewer", 1, "FAIL"),
        ("reviewer", 1, "PASS"),
    ]
    assert records[0]["failure_types"] == ["CITATION_MISMATCH"]
    assert records[0]["request_id"] == handoff.request_id
    assert "failure_types" not in records[1] or records[1]["failure_types"] == []
    assert logger.dropped_fields == [] and logger.dropped_events == []


def test_no_draft_text_reaches_the_log(data):
    records: list[dict] = []
    reviewer = Reviewer(corpus=data.corpus, logger=AllowListLogger(sink=records.append))
    reviewer(make(f"{GOOD} Call 9876543210 now, ZEBRAMARKER."))

    dumped = repr(records)
    assert "9876543210" not in dumped and "ZEBRAMARKER" not in dumped and FACT not in dumped


# ---------------------------------------------------------------------------
# The Reviewer and the drafter agree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("style", ["standard", "shorter"])
@pytest.mark.parametrize("disposition", ["SEND_READY", "HOLD_DISAGREEMENT", "REFUSAL"])
def test_every_draft_the_drafter_makes_from_a_real_answer_passes(data, real_answers, disposition, style):
    reviewer = Reviewer(corpus=data.corpus)
    drafter = Drafter(corpus=data.corpus)
    for facts in real_answers:
        draft = drafter.draft(disposition, facts, style=style).draft
        handoff = ReviewHandoff(
            request_id=new_request_id(), draft=draft, routing_note=NOTE, facts=facts
        )
        result = reviewer(handoff)
        assert result.verdict == "PASS", (facts.scheme_id, facts.section_title, disposition, style, result)


@pytest.mark.parametrize("style", ["standard", "shorter"])
def test_every_not_covered_and_ambiguous_draft_the_drafter_makes_passes(data, style):
    reviewer = Reviewer(corpus=data.corpus)
    drafter = Drafter(corpus=data.corpus)
    cases = [
        stub_facts(scenario="not_covered"),
        stub_facts(scenario="ambiguous"),
    ]
    for facts in cases:
        draft = drafter.draft("NOT_COVERED", facts, style=style).draft
        handoff = ReviewHandoff(request_id=new_request_id(), draft=draft, routing_note=NOTE, facts=facts)
        assert reviewer(handoff).verdict == "PASS", draft.text
    refusal = drafter.draft("REFUSAL", stub_facts(scenario="not_covered"), style=style).draft
    handoff = ReviewHandoff(
        request_id=new_request_id(), draft=refusal, routing_note=NOTE, facts=stub_facts(scenario="not_covered")
    )
    assert reviewer(handoff).verdict == "PASS", refusal.text


def test_with_no_outbound_check_supplied_the_rule_layer_still_blocks_advice(data):
    assert "ADVICE_LANGUAGE" in failures(Reviewer(corpus=data.corpus)(make(f"{FACT} You should buy more. {SOURCE}")))
