"""Phase 5: the routing note (Architecture section 8.3)."""

from __future__ import annotations

import json

import pytest

from agents.stubs import stub_compliance, stub_facts, stub_pattern
from constants import THEME_IDS
from orchestrator.disposition import decide
from orchestrator.routing import (
    COMPLIANCE_TEAM,
    FUNDS_TEAM,
    GENERAL_TEAM,
    THEME_TEAMS,
    build_routing_note,
)
from schemas import PiiCounts


def _note(facts=None, pattern=None, compliance=None, **kwargs):
    facts = facts or stub_facts()
    pattern = pattern or stub_pattern()
    compliance = compliance or stub_compliance()
    disposition = decide(facts, pattern, compliance).value
    return build_routing_note(disposition, facts, pattern, compliance, **kwargs)


def _theme(pattern, theme):
    return pattern.model_copy(update={"theme_id": theme})


# -- team --------------------------------------------------------------------


def test_every_theme_has_a_team():
    assert set(THEME_TEAMS) == set(THEME_IDS)


def test_a_covered_fact_question_goes_to_mutual_funds_support():
    note = _note(pattern=_theme(stub_pattern(), "other"))
    assert note.team == FUNDS_TEAM


@pytest.mark.parametrize("theme", THEME_IDS)
def test_not_covered_goes_to_the_theme_owner(theme):
    note = _note(facts=stub_facts(scenario="not_covered"), pattern=_theme(stub_pattern(), theme))
    assert note.team == THEME_TEAMS[theme]


def test_a_theme_with_no_clear_owner_goes_to_general_support():
    note = _note(facts=stub_facts(scenario="not_covered"), pattern=_theme(stub_pattern(), "other"))
    assert note.team == GENERAL_TEAM


def test_ambiguous_scheme_goes_to_mutual_funds_support():
    assert _note(facts=stub_facts(scenario="ambiguous")).team == FUNDS_TEAM


def test_advice_request_adds_compliance_review():
    note = _note(compliance=stub_compliance(scenario="advice_request"))
    assert note.team.endswith(COMPLIANCE_TEAM)
    assert note.team.split(" + ")[0] != COMPLIANCE_TEAM


def test_a_rising_issue_also_goes_to_the_theme_owner():
    pattern = _theme(stub_pattern(scenario="rising"), "money_movement")
    note = _note(pattern=pattern)
    assert note.team == f"{FUNDS_TEAM} + {THEME_TEAMS['money_movement']}"


def test_a_one_off_does_not_add_a_second_team():
    note = _note(pattern=_theme(stub_pattern(scenario="one_off"), "money_movement"))
    assert note.team == FUNDS_TEAM


def test_the_other_theme_never_adds_general_support_twice():
    note = _note(pattern=_theme(stub_pattern(scenario="recurring"), "other"))
    assert note.team == FUNDS_TEAM


# -- priority ----------------------------------------------------------------


@pytest.mark.parametrize("signal,priority", [("rising", "P1"), ("recurring", "P2"), ("one_off", "P3")])
def test_base_priority_follows_the_signal(signal, priority):
    note = _note(pattern=_theme(stub_pattern(scenario=signal), "fees_brokerage"))
    assert note.priority == priority


@pytest.mark.parametrize("signal,priority", [("rising", "P1"), ("recurring", "P1"), ("one_off", "P2")])
def test_money_movement_raises_the_priority_one_level(signal, priority):
    note = _note(pattern=_theme(stub_pattern(scenario=signal), "money_movement"))
    assert note.priority == priority


def test_a_raised_priority_says_why():
    note = _note(pattern=_theme(stub_pattern(scenario="one_off"), "money_movement"))
    assert "Raised one level" in note.reason


def test_no_raise_note_for_other_themes():
    note = _note(pattern=_theme(stub_pattern(scenario="one_off"), "reliability"))
    assert "Raised" not in note.reason


def test_a_failed_run_is_p2_and_goes_to_general_support():
    note = _note(facts=stub_facts(scenario="error"))
    assert (note.team, note.priority) == (GENERAL_TEAM, "P2")


# -- the reason --------------------------------------------------------------


def test_the_reason_states_count_window_and_theme():
    pattern = _theme(stub_pattern(scenario="recurring"), "fees_brokerage")
    note = _note(pattern=pattern)
    assert "recurring" in note.reason
    assert "fees_brokerage" in note.reason
    assert str(pattern.count_current) in note.reason
    assert str(pattern.count_prior) in note.reason
    assert pattern.window.start.isoformat() in note.reason
    assert pattern.window.end.isoformat() in note.reason
    assert "7 days" in note.reason


def test_a_recurring_issue_asks_for_a_known_issue_check():
    assert "known issue" in _note(pattern=stub_pattern(scenario="recurring")).reason


def test_a_theme_level_result_says_reviews_in_this_theme():
    pattern = stub_pattern(scenario="recurring").model_copy(update={"basis": "theme_level"})
    assert "reviews in this theme" in _note(pattern=pattern).reason


def test_the_pattern_signal_is_in_the_note_for_a_refusal_and_not_covered():
    refusal = _note(pattern=stub_pattern(scenario="rising"), compliance=stub_compliance(scenario="advice_request"))
    assert "rising" in refusal.reason and refusal.signals.pattern == "rising"
    not_covered = _note(facts=stub_facts(scenario="not_covered"), pattern=stub_pattern(scenario="rising"))
    assert "rising" in not_covered.reason


def test_pii_counts_are_in_the_note_without_values():
    note = _note(pii_manifest=PiiCounts(PAN=1, PHONE=2))
    assert "PAN 1" in note.reason and "PHONE 2" in note.reason
    assert "EMAIL" not in note.reason


def test_no_pii_sentence_when_nothing_was_redacted():
    assert "Redacted" not in _note(pii_manifest=PiiCounts()).reason


def test_advice_attempts_are_counted_in_the_note():
    assert "Advice requests in this session: 3." in _note(advice_attempts=3).reason
    assert "Advice requests" not in _note(advice_attempts=0).reason


def test_signals_use_status_signal_and_compliance_labels():
    note = _note(
        facts=stub_facts(scenario="not_covered"),
        pattern=stub_pattern(scenario="recurring"),
        compliance=stub_compliance(scenario="advice_request"),
    )
    assert note.signals.model_dump() == {
        "facts": "not_covered",
        "pattern": "recurring",
        "compliance": "advice_request",
    }


def test_a_failed_agent_has_the_error_signal():
    note = _note(pattern=stub_pattern(scenario="error"))
    assert note.signals.pattern == "error"


# -- disagreement and failure -----------------------------------------------


def test_a_disagreement_is_in_the_note_and_says_do_not_close():
    note = _note(pattern=stub_pattern(scenario="rising"))
    assert note.disagreement is not None
    assert "must not be closed" in note.disagreement
    assert note.reason.startswith("Do not close this ticket.")
    assert note.priority == "P1"


def test_no_disagreement_text_for_other_dispositions():
    for pattern in ("one_off", "recurring"):
        assert _note(pattern=stub_pattern(scenario=pattern)).disagreement is None
    assert _note(compliance=stub_compliance(scenario="advice_request")).disagreement is None


def test_the_failure_note_names_every_failed_agent_and_its_code():
    note = _note(facts=stub_facts(scenario="error"), pattern=stub_pattern(scenario="error"))
    assert "facts (STUB_FACTS_ERROR)" in note.reason
    assert "pattern (STUB_PATTERN_ERROR)" in note.reason
    assert "compliance" not in note.reason.split("Agent failure:")[1].split("No reply")[0]
    assert "No reply was drafted" in note.reason


def test_a_timeout_style_missing_result_is_named():
    note = build_routing_note("HOLD_AGENT_FAILURE", stub_facts(), None, stub_compliance())
    assert "pattern (AGENT_MISSING)" in note.reason


def test_the_note_holds_only_contract_fields():
    note = _note()
    assert set(json.loads(note.model_dump_json())) == {"team", "priority", "reason", "signals", "disagreement"}
