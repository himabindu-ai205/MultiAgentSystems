"""Phase 0 contract tests: every schema accepts a good sample and rejects a bad one."""

import pytest
from pydantic import ValidationError

from constants import ALLOWED_CITATIONS, SCHEME_SOURCE_URLS
from schemas import (
    ComplianceResult,
    Disposition,
    FactsResult,
    Flag,
    PatternResult,
    PiiCounts,
    RefinementEntry,
    ReplyDraft,
    ReviewerResult,
    RoutingNote,
    Searched,
    Session,
    Signals,
    Window,
    new_request_id,
)

MID_CAP_URL = SCHEME_SOURCE_URLS["mid_cap"]


def good_facts(**overrides):
    values = dict(
        status="covered",
        answer="HDFC Mid Cap Fund Direct Growth has an exit load of 1% if redeemed within 1 year.",
        citation=MID_CAP_URL,
        scheme_id="mid_cap",
        section_title="Exit load",
        searched=Searched(schemes=("mid_cap",), sections=("Exit load",)),
        source_fetched_at="2026-06-28T06:28:55+00:00",
    )
    values.update(overrides)
    return FactsResult(**values)


def good_window():
    return Window(start="2026-06-28", end="2026-07-04", days=7, anchor_date="2026-07-04")


# ---------------------------------------------------------------------------
# Citation contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", sorted(ALLOWED_CITATIONS))
def test_facts_result_accepts_each_of_the_five_source_urls(url):
    assert good_facts(citation=url).citation == url


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://example.com/some-page",
        "https://groww.in/mutual-funds/hdfc-flexi-cap-fund-direct-growth",
        MID_CAP_URL + "/",
        MID_CAP_URL + "?utm=x",
        MID_CAP_URL.replace("https", "http"),
        "",
    ],
)
def test_facts_result_rejects_a_citation_that_is_not_a_corpus_url(bad_url):
    with pytest.raises(ValidationError):
        good_facts(citation=bad_url)


def test_covered_result_without_a_citation_is_rejected():
    with pytest.raises(ValidationError):
        good_facts(citation=None)


def test_citation_cannot_be_swapped_after_construction():
    result = good_facts()
    with pytest.raises(ValidationError):
        result.citation = "https://example.com"  # frozen


# ---------------------------------------------------------------------------
# Facts rules
# ---------------------------------------------------------------------------


def test_not_covered_must_say_what_was_searched():
    ok = FactsResult(status="not_covered", searched=Searched(schemes=("mid_cap",), sections=("Exit load",)))
    assert ok.citation is None
    with pytest.raises(ValidationError):
        FactsResult(status="not_covered")
    with pytest.raises(ValidationError):
        FactsResult(
            status="not_covered",
            searched=Searched(sections=("Exit load",)),
            citation=MID_CAP_URL,
        )


def test_ambiguous_must_list_candidates():
    assert FactsResult(status="ambiguous", candidates=("large_cap", "gold_fof")).candidates
    with pytest.raises(ValidationError):
        FactsResult(status="ambiguous")


def test_error_result_needs_an_error_code_and_no_answer():
    assert FactsResult(status="error", error="TIMEOUT").error == "TIMEOUT"
    with pytest.raises(ValidationError):
        FactsResult(status="error")


def test_facts_result_rejects_unknown_fields_and_values():
    with pytest.raises(ValidationError):
        good_facts(surprise="x")
    with pytest.raises(ValidationError):
        good_facts(status="maybe")
    with pytest.raises(ValidationError):
        good_facts(scheme_id="flexi_cap")
    with pytest.raises(ValidationError):
        good_facts(section_title="Returns")


# ---------------------------------------------------------------------------
# Pattern
# ---------------------------------------------------------------------------


def good_pattern(**overrides):
    values = dict(
        status="ok",
        theme_id="fees_brokerage",
        signal="rising",
        count_current=40,
        count_prior=10,
        window=good_window(),
        basis="keyword_match",
        matched_review_ids=("2ce5bd6c39919177",),
    )
    values.update(overrides)
    return PatternResult(**values)


def test_pattern_result_good_sample_and_rejections():
    assert good_pattern().signal == "rising"
    with pytest.raises(ValidationError):
        good_pattern(signal="exploding")
    with pytest.raises(ValidationError):
        good_pattern(theme_id="billing")
    with pytest.raises(ValidationError):
        good_pattern(count_current=-1)
    with pytest.raises(ValidationError):
        good_pattern(window=None)
    with pytest.raises(ValidationError):
        PatternResult(status="error")


def test_pattern_result_only_holds_review_ids_never_review_text():
    with pytest.raises(ValidationError):
        good_pattern(matched_review_ids=("the app keeps crashing when I log in",))
    with pytest.raises(ValidationError):
        good_pattern(matched_review_ids=("ABCDE1234F",))


def test_window_must_not_end_before_it_starts():
    with pytest.raises(ValidationError):
        Window(start="2026-07-04", end="2026-06-28", days=7, anchor_date="2026-07-04")


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------


def test_compliance_result_good_samples():
    assert ComplianceResult(mode="inbound", status="ok", clean=True).clean
    flagged = ComplianceResult(
        mode="outbound",
        status="ok",
        clean=False,
        flags=(Flag(type="advice_in_text", detail="rule.stay_invested"),),
        pii_redacted=PiiCounts(PAN=1),
    )
    assert flagged.pii_redacted.PAN == 1


def test_compliance_clean_flag_must_agree_with_flags():
    with pytest.raises(ValidationError):
        ComplianceResult(mode="inbound", status="ok", clean=True,
                         flags=(Flag(type="advice_request", detail="rule.should_i"),))
    with pytest.raises(ValidationError):
        ComplianceResult(mode="inbound", status="ok", clean=False)
    with pytest.raises(ValidationError):
        ComplianceResult(mode="inbound", status="error", clean=True, error="X")


def test_flag_detail_cannot_hold_matched_user_text():
    with pytest.raises(ValidationError):
        Flag(type="pii_residual", detail="ABCDE1234F was found")
    with pytest.raises(ValidationError):
        Flag(type="advice_request", detail="you should redeem now")
    with pytest.raises(ValidationError):
        Flag(type="advice_request", detail="x" * 65)


def test_pii_counts_hold_counts_not_values():
    with pytest.raises(ValidationError):
        PiiCounts(PAN="ABCDE1234F")
    with pytest.raises(ValidationError):
        PiiCounts(PAN=-1)
    with pytest.raises(ValidationError):
        PiiCounts(PASSPORT=1)


# ---------------------------------------------------------------------------
# Orchestrator outputs and Reviewer
# ---------------------------------------------------------------------------


def test_orchestrator_output_models():
    draft = ReplyDraft(text="Exit load is 1%.", citation=MID_CAP_URL, last_checked="2026-06-28", kind="answer")
    assert draft.kind == "answer"
    note = RoutingNote(
        team="Mutual Funds Support",
        priority="P2",
        reason="Recurring fee question this week",
        signals=Signals(facts="covered", pattern="recurring", compliance="clean"),
    )
    assert note.priority == "P2"
    assert Disposition(value="SEND_READY").value == "SEND_READY"
    with pytest.raises(ValidationError):
        Disposition(value="SENT")
    with pytest.raises(ValidationError):
        RoutingNote(team="x", priority="P0", reason="r",
                    signals=Signals(facts="covered", pattern="rising", compliance="clean"))
    with pytest.raises(ValidationError):
        ReplyDraft(text="", kind="answer")


def test_a_bad_draft_is_still_representable_so_the_reviewer_can_fail_it():
    long_uncited = ReplyDraft(text="One. Two. Three. Four. Five.", kind="answer")
    assert long_uncited.citation is None
    off_corpus = ReplyDraft(text="See here.", citation="https://example.com", kind="answer")
    assert off_corpus.citation == "https://example.com"


def test_reviewer_result_rules():
    assert ReviewerResult(verdict="PASS", round=1).verdict == "PASS"
    assert ReviewerResult(verdict="FAIL", round=1, failure_types=("UNCITED_CLAIM",),
                          instruction="Add a citation link to sentence 2.").verdict == "FAIL"
    flagged = ReviewerResult(verdict="FAIL", round=2, failure_types=("PII_SURVIVED",), flagged=True)
    assert flagged.flagged
    with pytest.raises(ValidationError):
        ReviewerResult(verdict="PASS", round=1, failure_types=("UNCITED_CLAIM",))
    with pytest.raises(ValidationError):
        ReviewerResult(verdict="FAIL", round=1)
    with pytest.raises(ValidationError):
        ReviewerResult(verdict="FAIL", round=1, failure_types=("MADE_UP",))
    with pytest.raises(ValidationError):
        ReviewerResult(verdict="FAIL", round=1, failure_types=("PII_SURVIVED",), flagged=True)
    with pytest.raises(ValidationError):
        ReviewerResult(verdict="FAIL", round=3, failure_types=("PII_SURVIVED",))


# ---------------------------------------------------------------------------
# Session: no raw complaint
# ---------------------------------------------------------------------------


def test_session_good_sample(session):
    assert session.redacted_text.startswith("my withdrawal")
    assert session.approval.status == "pending"
    assert session.refinement.rounds_used == 0


@pytest.mark.parametrize(
    "name",
    ["raw_complaint", "raw_text", "raw", "complaint", "message", "text", "original_text", "user_message"],
)
def test_session_rejects_raw_text_under_any_other_name_on_construction(name):
    with pytest.raises(ValidationError):
        Session(request_id=new_request_id(), redacted_text="ok", **{name: "my PAN is ABCDE1234F"})


@pytest.mark.parametrize("name", ["raw_complaint", "raw_text", "complaint", "message", "text"])
def test_session_rejects_raw_text_under_any_other_name_on_assignment(session, name):
    with pytest.raises(ValueError):
        setattr(session, name, "my PAN is ABCDE1234F")
    assert not hasattr(session, name)


def test_session_rejects_unknown_fields_in_nested_state(session):
    with pytest.raises(ValidationError):
        Session(request_id=new_request_id(), redacted_text="ok", results={"raw_input": "x"})
    with pytest.raises(ValidationError):
        Session(request_id=new_request_id(), redacted_text="ok", reviewer={"notes": "x"})
    with pytest.raises(ValueError):
        session.reviewer.raw_notes = "x"


def test_session_has_no_field_that_looks_like_raw_text():
    fields = set(Session.model_fields)
    assert "redacted_text" in fields
    assert not {f for f in fields if "raw" in f or f in {"complaint", "message", "text", "input"}}


def test_session_validates_its_counters_and_ids(session):
    with pytest.raises(ValidationError):
        Session(request_id="not-a-uuid", redacted_text="ok")
    with pytest.raises(ValidationError):
        Session(request_id=new_request_id().upper(), redacted_text="ok")
    with pytest.raises(ValidationError):
        session.refinement.rounds_used = 6
    with pytest.raises(ValidationError):
        session.approval.draft_hash = "not-a-hash"
    session.refinement.rounds_used = 5  # the cap itself is allowed
    with pytest.raises(ValidationError):
        session.approval.mcp_tools_called = ("send_email",)


def test_refinement_entry_stores_category_not_feedback_text():
    entry = RefinementEntry(category="style", agents_fired=("orchestrator",))
    assert entry.agents_cached == ()
    with pytest.raises(ValidationError):
        RefinementEntry(category="make it shorter please", agents_fired=("orchestrator",))
    with pytest.raises(ValidationError):
        RefinementEntry(category="style", agents_fired=("orchestrator",), feedback="shorter please")


# ---------------------------------------------------------------------------
# Safe printing
# ---------------------------------------------------------------------------


def test_session_repr_and_str_show_only_redacted_text_ids_and_counters():
    session = Session(
        request_id=new_request_id(),
        redacted_text="withdrawal stuck, PAN is [PAN]",
        draft=ReplyDraft(text="DRAFT-BODY-MARKER", kind="not_covered"),
        routing_note=RoutingNote(
            team="TEAM-MARKER",
            priority="P3",
            reason="REASON-MARKER",
            signals=Signals(facts="not_covered", pattern="one_off", compliance="clean"),
        ),
        disposition="NOT_COVERED",
    )
    session.results.pattern = good_pattern()

    for shown in (repr(session), str(session), f"{session}", f"{session!r}"):
        assert session.request_id in shown
        assert "withdrawal stuck, PAN is [PAN]" in shown
        assert "2ce5bd6c39919177" in shown
        assert "NOT_COVERED" in shown
        for hidden in ("DRAFT-BODY-MARKER", "TEAM-MARKER", "REASON-MARKER"):
            assert hidden not in shown


def test_session_repr_never_contains_a_raw_value_that_was_not_stored(session):
    """Raw text can't be stored, so it can't be printed."""
    raw = "ABCDE1234F"
    with pytest.raises(ValueError):
        session.raw_complaint = f"my PAN is {raw}"
    assert raw not in repr(session)
    assert raw not in str(session.model_dump())


# ---------------------------------------------------------------------------
# Phase 7 additions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["EMPTY_DRAFT", "COMPLIANCE_CHECK_FAILED"])
def test_reviewer_result_accepts_the_two_fail_closed_codes(code):
    assert ReviewerResult(verdict="FAIL", round=1, failure_types=(code,)).failure_types == (code,)


def _handoff_parts():
    draft = ReplyDraft(text="A reply.", kind="not_covered")
    note = RoutingNote(
        team="General Support",
        priority="P3",
        reason="one off",
        signals=Signals(facts="not_covered", pattern="one_off", compliance="clean"),
    )
    return draft, note


def test_review_handoff_holds_the_draft_the_note_and_the_round():
    from schemas import ReviewHandoff

    draft, note = _handoff_parts()
    handoff = ReviewHandoff(request_id=new_request_id(), draft=draft, routing_note=note)
    assert handoff.round == 1 and handoff.facts is None


def test_review_handoff_rejects_a_third_round_and_unknown_fields():
    from schemas import ReviewHandoff

    draft, note = _handoff_parts()
    with pytest.raises(ValidationError):
        ReviewHandoff(request_id=new_request_id(), draft=draft, routing_note=note, round=3)
    with pytest.raises(ValidationError):
        ReviewHandoff(request_id=new_request_id(), draft=draft, routing_note=note, raw_complaint="x")


# ---------------------------------------------------------------------------
# Phase 8: what the human has asked for so far
# ---------------------------------------------------------------------------


def test_a_new_session_has_no_refinement_choices_yet(session):
    state = session.refinement
    assert (state.style, state.scheme_override, state.theme_hint, state.window_shift_weeks) == (
        "standard",
        None,
        None,
        0,
    )


def test_refinement_choices_accept_only_known_values(session):
    session.refinement.style = "shorter"
    session.refinement.scheme_override = "mid_cap"
    session.refinement.theme_hint = "fees_brokerage"
    session.refinement.window_shift_weeks = 1
    for field, bad in [
        ("style", "friendlier"),
        ("scheme_override", "nifty_50"),
        ("theme_hint", "weather"),
        ("window_shift_weeks", -1),
        ("window_shift_weeks", 53),
    ]:
        with pytest.raises(ValidationError):
            setattr(session.refinement, field, bad)


def test_refinement_state_still_cannot_hold_feedback_text(session):
    with pytest.raises(ValidationError):
        session.refinement.feedback = "make it shorter"
