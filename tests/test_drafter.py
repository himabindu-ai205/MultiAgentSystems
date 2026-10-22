"""Phase 5: the reply drafter (Architecture section 8.2) and the compose step."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from agents.compliance import ComplianceAgent
from agents.facts import FactsAgent
from agents.stubs import stub_compliance, stub_facts, stub_pattern
from constants import MAX_REPLY_SENTENCES, SCHEME_SOURCE_URLS
from logging_allowlist import AllowListLogger
from orchestrator.compose import compose
from orchestrator.drafter import (
    SCHEME_LABELS,
    DraftOutcome,
    DraftRequest,
    Drafter,
    build_prompt,
    format_last_checked,
    pack_answer,
)
from schemas import FactsResult, PiiCounts, Searched, new_request_id
from textutil import count_sentences, numbers_in, urls_in

SRC = Path(__file__).resolve().parents[1] / "src"
LARGE_URL = SCHEME_SOURCE_URLS["large_cap"]
LARGE_ANSWER = "HDFC Large Cap Fund Direct Growth has an exit load of 1% if redeemed within 1 year."


@pytest.fixture(scope="module")
def drafter(data):
    return Drafter(corpus=data.corpus)


class Writer:
    def __init__(self, text=None, exc=None):
        self.text, self.exc, self.requests = text, exc, []

    def write(self, request):
        self.requests.append(request)
        if self.exc:
            raise self.exc
        return self.text


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def test_send_ready_draft_has_the_fact_one_link_and_the_date(drafter):
    outcome = drafter.draft("SEND_READY", stub_facts())
    draft = outcome.draft
    assert draft.kind == "answer"
    assert draft.citation == LARGE_URL
    assert draft.last_checked == "28-Jun-2026"
    assert LARGE_ANSWER.rstrip(".") in draft.text
    assert urls_in(draft.text) == [LARGE_URL]
    assert "last checked 28-Jun-2026" in draft.text
    assert count_sentences(draft.text) <= MAX_REPLY_SENTENCES
    assert (outcome.wording, outcome.reason_code) == ("template", None)


def test_hold_disagreement_draft_is_a_cited_fact_with_its_own_kind(drafter):
    draft = drafter.draft("HOLD_DISAGREEMENT", stub_facts()).draft
    assert draft.kind == "escalation_hold"
    assert draft.citation == LARGE_URL and draft.last_checked == "28-Jun-2026"


def test_not_covered_draft_says_so_names_what_was_searched_and_has_no_link(drafter):
    facts = stub_facts(scenario="not_covered")
    draft = drafter.draft("NOT_COVERED", facts).draft
    assert draft.kind == "not_covered"
    assert draft.citation is None and draft.last_checked is None
    assert "can't answer" in draft.text and "don't want to guess" in draft.text
    assert "Key fund facts" in draft.text and "Exit load" in draft.text
    assert "all five HDFC scheme pages" in draft.text
    assert not urls_in(draft.text)
    assert count_sentences(draft.text) <= MAX_REPLY_SENTENCES


def test_not_covered_names_a_narrower_search(drafter):
    facts = FactsResult(
        status="not_covered",
        searched=Searched(schemes=("mid_cap",), sections=("Benchmark",)),
    )
    text = drafter.draft("NOT_COVERED", facts).draft.text
    assert "HDFC Mid Cap Fund" in text and "Benchmark" in text and "all five" not in text


def test_ambiguous_draft_asks_which_scheme(drafter):
    draft = drafter.draft("NOT_COVERED", stub_facts(scenario="ambiguous")).draft
    assert draft.kind == "not_covered"
    assert draft.text.startswith("Which scheme do you mean")
    assert "HDFC Large Cap Fund" in draft.text and "HDFC Gold ETF Fund of Fund" in draft.text
    assert not urls_in(draft.text)


def test_refusal_without_a_fact_has_no_link_and_no_number(drafter):
    draft = drafter.draft("REFUSAL", stub_facts(scenario="not_covered")).draft
    assert draft.kind == "refusal"
    assert draft.citation is None
    assert not urls_in(draft.text) and not numbers_in(draft.text)
    assert "can't give investment advice" in draft.text


def test_refusal_may_restate_a_covered_fact_with_its_citation(drafter):
    draft = drafter.draft("REFUSAL", stub_facts()).draft
    assert draft.kind == "refusal"
    assert draft.citation == LARGE_URL and draft.last_checked == "28-Jun-2026"
    assert urls_in(draft.text) == [LARGE_URL]
    assert count_sentences(draft.text) <= MAX_REPLY_SENTENCES


def test_drafting_never_changes_the_disposition_it_was_given(drafter):
    # The drafter has no way to return a disposition, only a draft.
    assert set(DraftOutcome.__dataclass_fields__) == {"draft", "wording", "reason_code"}


# ---------------------------------------------------------------------------
# Rules that hold for every real answer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("style", ["standard", "shorter"])
@pytest.mark.parametrize("disposition", ["SEND_READY", "HOLD_DISAGREEMENT", "REFUSAL"])
def test_every_real_answer_drafts_within_the_rules(drafter, data, real_answers, disposition, style):
    outbound = ComplianceAgent(corpus=data.corpus)
    limit = 2 if style == "shorter" else MAX_REPLY_SENTENCES
    for facts in real_answers:
        outcome = drafter.draft(disposition, facts, style=style)
        draft = outcome.draft
        assert draft is not None, (facts.scheme_id, facts.section_title, outcome.reason_code)
        assert 1 <= count_sentences(draft.text) <= limit, draft.text
        assert outbound.outbound(draft.text).clean, draft.text
        if draft.citation:
            assert urls_in(draft.text) == [facts.citation], draft.text
            assert draft.last_checked and draft.last_checked in draft.text
            # The fact is kept whole, every number included.
            assert numbers_in(facts.answer) <= numbers_in(draft.text), draft.text
        else:
            assert not urls_in(draft.text)


def test_a_long_answer_is_packed_not_cut(drafter, real_answers):
    key_facts = next(r for r in real_answers if r.section_title == "Key fund facts")
    assert count_sentences(key_facts.answer) > MAX_REPLY_SENTENCES
    text = drafter.draft("SEND_READY", key_facts).draft.text
    assert count_sentences(text) <= MAX_REPLY_SENTENCES
    for number in numbers_in(key_facts.answer):
        assert number in numbers_in(text)


def test_pack_answer_keeps_a_short_answer_as_it_is():
    assert pack_answer("One. Two.", 2) == ["One.", "Two."]
    assert pack_answer("One thing", 1) == ["One thing."]


def test_pack_answer_joins_a_long_answer_into_one_sentence():
    packed = pack_answer("NAV: 1. Size: 2. Plan: Direct.", 2)
    assert packed == ["NAV: 1; Size: 2; Plan: Direct."]
    assert count_sentences(packed[0]) == 1


# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------


def test_shorter_is_never_longer_than_standard(drafter, real_answers):
    for facts in real_answers[:10]:
        standard = drafter.draft("SEND_READY", facts).draft.text
        shorter = drafter.draft("SEND_READY", facts, style="shorter").draft.text
        assert len(shorter) <= len(standard)
        assert count_sentences(shorter) <= count_sentences(standard)


def test_shorter_keeps_the_citation_and_the_date(drafter):
    draft = drafter.draft("SEND_READY", stub_facts(), style="shorter").draft
    assert urls_in(draft.text) == [LARGE_URL] and "28-Jun-2026" in draft.text


def test_shorter_not_covered_still_names_what_was_searched(drafter):
    text = drafter.draft("NOT_COVERED", stub_facts(scenario="not_covered"), style="shorter").draft.text
    assert "Exit load" in text and count_sentences(text) <= 2


def test_shorter_refusal_is_one_sentence_without_the_fact(drafter):
    draft = drafter.draft("REFUSAL", stub_facts(), style="shorter").draft
    assert count_sentences(draft.text) == 1 and draft.citation is None


def test_an_unknown_style_gives_no_draft(drafter):
    outcome = drafter.draft("SEND_READY", stub_facts(), style="longer")
    assert outcome.draft is None and outcome.reason_code == "DRAFT_BAD_INPUT"


# ---------------------------------------------------------------------------
# Inputs that must not produce a draft
# ---------------------------------------------------------------------------


def test_hold_agent_failure_makes_no_draft(drafter):
    outcome = drafter.draft("HOLD_AGENT_FAILURE", stub_facts())
    assert outcome.draft is None


@pytest.mark.parametrize("disposition", ["SEND_READY", "HOLD_DISAGREEMENT"])
def test_an_answer_disposition_needs_a_covered_fact(drafter, disposition):
    assert drafter.draft(disposition, stub_facts(scenario="not_covered")).draft is None


def test_not_covered_disposition_needs_a_not_covered_or_ambiguous_fact(drafter):
    assert drafter.draft("NOT_COVERED", stub_facts()).draft is None


def test_missing_facts_make_no_draft(drafter):
    assert drafter.draft("SEND_READY", None).draft is None


def test_a_bad_fetched_at_date_blocks_an_answer_but_not_a_refusal(drafter):
    facts = stub_facts().model_copy(update={"source_fetched_at": "garbage"})
    assert drafter.draft("SEND_READY", facts).reason_code == "DRAFT_BAD_FETCHED_AT"
    refusal = drafter.draft("REFUSAL", facts).draft
    assert refusal is not None and refusal.citation is None and not urls_in(refusal.text)


def test_a_template_that_trips_compliance_gives_no_draft(drafter):
    facts = stub_facts().model_copy(update={"answer": "You should sell this fund and keep nothing."})
    outcome = drafter.draft("SEND_READY", facts)
    assert outcome.draft is None and outcome.reason_code == "DRAFT_FLAGGED"


def test_max_sentences_below_two_is_rejected():
    with pytest.raises(ValueError):
        Drafter(max_sentences=1)


# ---------------------------------------------------------------------------
# The optional model
# ---------------------------------------------------------------------------

GOOD = (
    f"HDFC Large Cap Fund Direct Growth charges an exit load of 1% if you redeem within 1 year. "
    f"Source: {LARGE_URL} (last checked 28-Jun-2026)."
)


def test_good_model_wording_is_used(data):
    writer = Writer(GOOD)
    outcome = Drafter(writer=writer, corpus=data.corpus).draft("SEND_READY", stub_facts())
    assert outcome.wording == "model" and outcome.reason_code is None
    assert outcome.draft.text == GOOD
    assert outcome.draft.citation == LARGE_URL and outcome.draft.last_checked == "28-Jun-2026"
    assert len(writer.requests) == 1


@pytest.mark.parametrize(
    "bad,code",
    [
        ("", "WRITER_BAD_OUTPUT"),
        ("   ", "WRITER_BAD_OUTPUT"),
        (None, "WRITER_BAD_OUTPUT"),
        (42, "WRITER_BAD_OUTPUT"),
        # Four sentences.
        (f"One. Two. Three. Four {LARGE_URL} 28-Jun-2026 1 1", "WRITER_BAD_OUTPUT"),
        # Wrong link.
        (GOOD.replace(LARGE_URL, SCHEME_SOURCE_URLS["mid_cap"]), "WRITER_BAD_OUTPUT"),
        # Invented link as well.
        (GOOD + " More at https://example.com/x.", "WRITER_BAD_OUTPUT"),
        # No link.
        ("HDFC Large Cap Fund has an exit load of 1% within 1 year (last checked 28-Jun-2026).", "WRITER_BAD_OUTPUT"),
        # Link twice.
        (GOOD + f" Again: {LARGE_URL}", "WRITER_BAD_OUTPUT"),
        # Missing date.
        (f"The exit load is 1% within 1 year. Source: {LARGE_URL}.", "WRITER_BAD_OUTPUT"),
        # A number the corpus did not give.
        (GOOD.replace("1%", "2%"), "WRITER_BAD_OUTPUT"),
        # A number dropped.
        (
            f"The exit load applies if you redeem soon. Source: {LARGE_URL} (last checked 28-Jun-2026).",
            "WRITER_BAD_OUTPUT",
        ),
        # Advice in the wording.
        (GOOD + " You should stay invested longer.", "WRITER_BAD_OUTPUT"),
        # A return claim.
        (GOOD.replace("exit load", "exit load, and the fund will give 15% returns,"), "WRITER_BAD_OUTPUT"),
    ],
)
def test_bad_model_wording_falls_back_to_the_template(data, bad, code):
    template = Drafter(corpus=data.corpus).draft("SEND_READY", stub_facts()).draft.text
    outcome = Drafter(writer=Writer(bad), corpus=data.corpus).draft("SEND_READY", stub_facts())
    assert outcome.wording == "template_fallback"
    assert outcome.reason_code == code
    assert outcome.draft.text == template


def test_a_failing_model_falls_back_and_keeps_no_message(data):
    writer = Writer(exc=RuntimeError("secret detail"))
    outcome = Drafter(writer=writer, corpus=data.corpus).draft("SEND_READY", stub_facts())
    assert outcome.wording == "template_fallback" and outcome.reason_code == "WRITER_FAILED"
    assert "secret" not in repr(outcome)


def test_a_model_cannot_add_a_link_to_a_not_covered_reply(data):
    text = f"I can't answer this. See {LARGE_URL}."
    outcome = Drafter(writer=Writer(text), corpus=data.corpus).draft("NOT_COVERED", stub_facts(scenario="not_covered"))
    assert outcome.wording == "template_fallback"


def test_a_model_cannot_add_a_number_to_a_refusal_with_no_fact(data):
    text = "I can't give advice or say what the fund will return, but similar funds made 12%."
    outcome = Drafter(writer=Writer(text), corpus=data.corpus).draft("REFUSAL", stub_facts(scenario="not_covered"))
    assert outcome.wording == "template_fallback"


def test_a_model_can_rephrase_a_not_covered_reply(data):
    text = "I can't find this in the HDFC scheme pages I have, so I won't guess."
    outcome = Drafter(writer=Writer(text), corpus=data.corpus).draft("NOT_COVERED", stub_facts(scenario="not_covered"))
    assert outcome.wording == "model" and outcome.draft.text == text


def test_the_model_is_not_called_when_there_is_nothing_to_draft(data):
    writer = Writer(GOOD)
    drafter = Drafter(writer=writer, corpus=data.corpus)
    drafter.draft("HOLD_AGENT_FAILURE", stub_facts())
    drafter.draft("SEND_READY", stub_facts(scenario="not_covered"))
    assert writer.requests == []


def test_the_model_request_holds_no_complaint_text(data):
    writer = Writer(GOOD)
    Drafter(writer=writer, corpus=data.corpus).draft("SEND_READY", stub_facts())
    request = writer.requests[0]
    assert set(DraftRequest.__dataclass_fields__) == {
        "kind", "style", "max_sentences", "facts", "citation", "last_checked",
        "scheme_names", "section_titles", "template",
        # The Reviewer's named failure on the one rework round: fixed phrases and counts.
        "rework",
    }
    assert request.citation == LARGE_URL and request.max_sentences == MAX_REPLY_SENTENCES
    assert request.facts == (LARGE_ANSWER,)
    # The drafter takes no complaint argument at all.
    assert list(inspect.signature(Drafter.draft).parameters) == ["self", "disposition", "facts", "style", "rework"]


def test_the_prompt_carries_the_rules_the_link_and_the_template(data):
    writer = Writer(GOOD)
    Drafter(writer=writer, corpus=data.corpus).draft("SEND_READY", stub_facts())
    prompt = build_prompt(writer.requests[0])
    assert LARGE_URL in prompt and "28-Jun-2026" in prompt
    assert "at most 3 sentences" in prompt and "Do not give advice" in prompt
    assert writer.requests[0].template in prompt


def test_the_prompt_for_a_reply_without_a_fact_forbids_links(data):
    writer = Writer("x")
    Drafter(writer=writer, corpus=data.corpus).draft("NOT_COVERED", stub_facts(scenario="not_covered"))
    assert "Do not include any link." in build_prompt(writer.requests[0])


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-06-28T06:28:55+00:00", "28-Jun-2026"),
        ("2026-06-28T23:30:00-05:00", "29-Jun-2026"),
        ("2026-01-05T00:00:00Z", "05-Jan-2026"),
        ("2026-12-31", "31-Dec-2026"),
    ],
)
def test_last_checked_is_the_utc_date(value, expected):
    assert format_last_checked(value) == expected


@pytest.mark.parametrize("value", [None, "", "  ", "yesterday", 5, "2026-13-45"])
def test_a_bad_date_gives_none(value):
    assert format_last_checked(value) is None


# ---------------------------------------------------------------------------
# Compose: merge, draft and route together
# ---------------------------------------------------------------------------


def _run(drafter, facts=None, pattern=None, compliance=None, **kwargs):
    events = []
    logger = AllowListLogger(sink=events.append)
    outcome = compose(
        new_request_id(),
        facts or stub_facts(),
        pattern or stub_pattern(),
        compliance or stub_compliance(),
        drafter=drafter,
        logger=logger,
        **kwargs,
    )
    return outcome, events, logger


def test_compose_send_ready(drafter):
    outcome, events, _ = _run(drafter)
    assert outcome.disposition.value == "SEND_READY"
    assert outcome.draft.kind == "answer"
    assert outcome.routing_note.team == "Mutual Funds Support"
    assert events[0]["event"] == "disposition" and events[0]["value"] == "SEND_READY"


def test_facts_covered_and_pattern_rising_is_held_with_a_note_that_says_so(drafter):
    outcome, _, _ = _run(drafter, pattern=stub_pattern(scenario="rising"))
    assert outcome.disposition.value == "HOLD_DISAGREEMENT"
    assert outcome.draft is not None and outcome.draft.kind == "escalation_hold"
    assert "must not be closed" in outcome.routing_note.disagreement
    assert outcome.routing_note.reason.startswith("Do not close this ticket.")


def test_an_agent_error_gives_hold_agent_failure_and_no_draft(drafter):
    for kwargs in (
        {"facts": stub_facts(scenario="error")},
        {"pattern": stub_pattern(scenario="error")},
        {"compliance": stub_compliance(scenario="error")},
    ):
        outcome, _, _ = _run(drafter, **kwargs)
        assert outcome.disposition.value == "HOLD_AGENT_FAILURE"
        assert outcome.draft is None
        assert outcome.failures and "No reply was drafted" in outcome.routing_note.reason


def test_a_failure_does_not_call_the_model(data):
    writer = Writer(GOOD)
    _run(Drafter(writer=writer, corpus=data.corpus), facts=stub_facts(scenario="error"))
    assert writer.requests == []


def test_refusal_with_a_rising_pattern_keeps_the_pattern_in_the_note_only(drafter):
    outcome, _, _ = _run(
        drafter, pattern=stub_pattern(scenario="rising"), compliance=stub_compliance(scenario="advice_request")
    )
    assert outcome.disposition.value == "REFUSAL"
    assert "rising" in outcome.routing_note.reason
    assert "rising" not in outcome.draft.text.lower()


def test_not_covered_with_a_rising_pattern(drafter):
    outcome, _, _ = _run(
        drafter, facts=stub_facts(scenario="not_covered"), pattern=stub_pattern(scenario="rising")
    )
    assert outcome.disposition.value == "NOT_COVERED"
    assert "corpus" not in outcome.draft.text  # the reply never mentions internals
    assert outcome.routing_note.signals.pattern == "rising"


def test_an_unusable_draft_becomes_an_orchestrator_failure_never_a_pass(data):
    facts = stub_facts().model_copy(update={"source_fetched_at": "garbage"})
    outcome, _, _ = _run(Drafter(corpus=data.corpus), facts=facts)
    assert outcome.disposition.value == "HOLD_AGENT_FAILURE"
    assert outcome.draft is None
    assert "orchestrator (DRAFT_BAD_FETCHED_AT)" in outcome.routing_note.reason


def test_compose_passes_style_pii_counts_and_advice_attempts(drafter):
    outcome, _, _ = _run(
        drafter, style="shorter", pii_manifest=PiiCounts(PAN=1), advice_attempts=2
    )
    assert count_sentences(outcome.draft.text) <= 2
    assert "PAN 1" in outcome.routing_note.reason
    assert "Advice requests in this session: 2." in outcome.routing_note.reason


def test_the_disposition_event_passes_the_allow_list_unchanged(drafter):
    _, events, logger = _run(drafter, pattern=stub_pattern(scenario="rising"))
    assert logger.dropped_events == [] and logger.dropped_fields == []
    assert set(events[0]) == {"event", "ts", "request_id", "value", "signals"}
    assert events[0]["signals"] == {"facts": "covered", "pattern": "rising", "compliance": "clean"}


def test_nothing_is_logged_without_a_logger(drafter):
    outcome = compose(
        new_request_id(), stub_facts(), stub_pattern(), stub_compliance(), drafter=drafter
    )
    assert outcome.disposition.value == "SEND_READY"


def test_every_draft_the_pipeline_makes_has_at_most_three_sentences_and_one_link(drafter, real_answers):
    for facts in real_answers:
        for pattern in ("one_off", "recurring", "rising"):
            for compliance in ("clean", "advice_request"):
                outcome, _, _ = _run(
                    drafter,
                    facts=facts,
                    pattern=stub_pattern(scenario=pattern),
                    compliance=stub_compliance(scenario=compliance),
                )
                assert outcome.draft is not None
                assert count_sentences(outcome.draft.text) <= MAX_REPLY_SENTENCES
                assert len(urls_in(outcome.draft.text)) <= 1


# ---------------------------------------------------------------------------
# Privacy and structure
# ---------------------------------------------------------------------------


def test_a_pii_value_in_the_complaint_never_reaches_a_draft_or_a_log(data):
    """The complaint is not an input to drafting, so a PII value cannot leak through it."""
    secret = "ABCDE1234F"
    writer = Writer(GOOD)
    events = []
    outcome = compose(
        new_request_id(),
        stub_facts(),
        stub_pattern(),
        stub_compliance(),
        drafter=Drafter(writer=writer, corpus=data.corpus),
        pii_manifest=PiiCounts(PAN=1),
        logger=AllowListLogger(sink=events.append),
    )
    everything = " ".join(
        [outcome.draft.text, outcome.routing_note.model_dump_json(), repr(events), repr(writer.requests)]
    )
    assert secret not in everything


def test_the_drafter_does_not_import_the_other_agents_except_compliance_for_its_own_check():
    tree = ast.parse((SRC / "orchestrator" / "drafter.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not (imported & {"agents.facts", "agents.pattern", "agents.stubs", "ingress", "ingress.redactor"})


def test_orchestrator_modules_never_write_files_or_touch_the_network():
    banned_modules = {"requests", "urllib", "socket", "http", "httpx", "subprocess"}
    banned_calls = {"open", "print"}
    for name in ("disposition", "routing", "drafter", "compose"):
        tree = ast.parse((SRC / "orchestrator" / f"{name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not {a.name.split(".")[0] for a in node.names} & banned_modules, name
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in banned_modules, name
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in banned_calls, (name, node.func.id)
            elif isinstance(node, ast.Attribute):
                assert node.attr not in {"write_text", "write_bytes"}, name

# ---------------------------------------------------------------------------
# Rework: the Reviewer's named failure goes back to the drafter (Phase 7)
# ---------------------------------------------------------------------------

REWORK = "Sentence 1 states a fact without the source link. Give the one source link, or remove the claim."


def test_a_rework_instruction_reaches_the_model_request_and_its_prompt(data):
    writer = Writer(GOOD)
    Drafter(writer=writer, corpus=data.corpus).draft("SEND_READY", stub_facts(), rework=REWORK)

    request = writer.requests[0]
    assert request.rework == REWORK
    assert REWORK in build_prompt(request)


def test_without_a_rework_the_request_and_prompt_say_nothing_about_one(data):
    writer = Writer(GOOD)
    Drafter(writer=writer, corpus=data.corpus).draft("SEND_READY", stub_facts())

    assert writer.requests[0].rework is None
    assert "reviewer" not in build_prompt(writer.requests[0]).lower()


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_a_blank_rework_is_the_same_as_none(data, blank):
    writer = Writer(GOOD)
    Drafter(writer=writer, corpus=data.corpus).draft("SEND_READY", stub_facts(), rework=blank)
    assert writer.requests[0].rework is None


def test_a_rework_never_changes_the_template_or_the_disposition(data):
    drafter = Drafter(corpus=data.corpus)
    plain = drafter.draft("SEND_READY", stub_facts())
    reworked = drafter.draft("SEND_READY", stub_facts(), rework=REWORK)
    assert reworked.draft == plain.draft


def test_compose_passes_the_rework_to_the_drafter(data):
    writer = Writer(GOOD)
    compose(
        new_request_id(),
        stub_facts(),
        stub_pattern(scenario="one_off"),
        stub_compliance(),
        drafter=Drafter(writer=writer, corpus=data.corpus),
        rework=REWORK,
    )
    assert writer.requests[0].rework == REWORK
