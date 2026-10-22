"""Phase 8: the human view shows what the person needs, and offers only the controls that are safe."""

from __future__ import annotations

import pytest

from agents.stubs import stub_compliance, stub_facts, stub_pattern
from constants import MAX_REFINEMENT_ROUNDS, SCHEME_SOURCE_URLS
from orchestrator.dispatch import Orchestrator
from orchestrator.drafter import DraftOutcome, Drafter
from orchestrator.graph import AgentSet
from refinement.refiner import Refiner
from refinement.router import RefinementRouter
from refinement.view import HumanView, build_view, render
from reviewer.gate import Reviewer
from schemas import ReplyDraft

QUESTION = "What is the exit load of the HDFC Large Cap fund?"
URL = SCHEME_SOURCE_URLS["large_cap"]
FACT = "HDFC Large Cap Fund Direct Growth has an exit load of 1% if redeemed within 1 year."
SOURCE = f"Source: {URL} (last checked 28-Jun-2026)."
GOOD = ReplyDraft(text=f"{FACT} {SOURCE}", kind="answer", citation=URL, last_checked="28-Jun-2026")
ADVICE = ReplyDraft(text=f"{FACT} You should buy more units. {SOURCE}", kind="answer", citation=URL, last_checked="28-Jun-2026")


class Scripted:
    def __init__(self, *drafts):
        self.queue = list(drafts)

    def draft(self, disposition, facts, *, style="standard", rework=None):
        return DraftOutcome(self.queue.pop(0))


def stack(*, facts="covered", pattern="one_off", compliance="clean", drafter=None, reviewer=True):
    agents = AgentSet(
        facts=lambda *a, **k: stub_facts(scenario=facts),
        pattern=lambda *a, **k: stub_pattern(scenario=pattern),
        compliance=lambda *a, **k: stub_compliance(scenario=compliance),
    )
    orchestrator = Orchestrator(
        agents, drafter or Drafter(), reviewer=Reviewer() if reviewer else None
    )
    return orchestrator, Refiner(orchestrator, RefinementRouter())


def first_view(question=QUESTION, **kwargs):
    orchestrator, refiner = stack(**kwargs)
    run = orchestrator.submit(question)
    return orchestrator, refiner, run, build_view(run.session, run=run)


# ---------------------------------------------------------------------------
# What the person sees
# ---------------------------------------------------------------------------


def test_the_view_has_everything_the_plan_lists():
    _, _, run, view = first_view()
    text = render(view)

    assert isinstance(view, HumanView)
    assert view.complaint == QUESTION and QUESTION in text  # the redacted complaint
    assert view.draft_text == run.session.draft.text and run.session.draft.text in text
    assert view.citation == URL and URL in text
    assert view.last_checked == "28-Jun-2026" and "28-Jun-2026" in text
    assert view.team == run.session.routing_note.team and view.team in text
    assert view.priority == run.session.routing_note.priority and view.priority in text
    assert view.verdict == "PASS" and "PASS" in text
    assert view.agents_fired == ("facts", "pattern", "compliance", "orchestrator")
    for agent in view.agents_fired:
        assert agent in text


def test_the_routing_reason_is_shown_so_a_person_can_check_the_priority():
    _, _, run, view = first_view()
    assert view.routing_reason == run.session.routing_note.reason
    assert run.session.routing_note.reason in render(view)


def test_a_disagreement_between_agents_is_shown_as_a_message():
    _, _, run, view = first_view(pattern="rising")
    assert run.session.disposition == "HOLD_DISAGREEMENT"
    assert view.disagreement and view.disagreement == run.session.routing_note.disagreement
    assert view.disagreement in render(view)


def test_no_draft_is_shown_as_such_when_an_agent_failed():
    _, _, run, view = first_view(facts="error")
    text = render(view)
    assert view.draft_text is None and run.session.disposition == "HOLD_AGENT_FAILURE"
    assert "no draft" in text.lower()
    assert "approve" not in view.controls


def test_a_notice_from_the_router_is_shown():
    orchestrator, _, run, _ = first_view()
    view = build_view(run.session, run=run, notice="Please rephrase.")
    assert "Please rephrase." in render(view)


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------


def test_a_passed_draft_offers_refine_approve_and_discard_with_the_remaining_count():
    _, _, _, view = first_view()
    assert view.controls == ("refine", "approve", "discard")
    assert view.refinements_left == MAX_REFINEMENT_ROUNDS
    assert "Refine (5 left)" in render(view)


def test_the_remaining_count_goes_down_after_a_refinement():
    orchestrator, refiner, run, _ = first_view()
    refiner.refine(run.session, "Shorter")
    view = build_view(run.session)
    assert view.refinements_left == 4 and "Refine (4 left)" in render(view)


def test_a_flagged_draft_shows_no_approve_control_but_can_still_be_refined_or_discarded():
    orchestrator, refiner = stack(drafter=Scripted(ADVICE, ADVICE))
    run = orchestrator.submit(QUESTION)
    view = build_view(run.session, run=run)

    assert run.session.reviewer.flagged
    assert view.flagged and view.verdict == "FAIL"
    assert view.controls == ("refine", "discard")
    text = render(view)
    assert "Approve" not in text and "flagged" in text.lower()
    assert "ADVICE_LANGUAGE" in text  # the reason, as a code


def test_a_draft_the_reviewer_never_saw_cannot_be_approved():
    _, _, run, view = first_view(reviewer=False)
    assert view.verdict == "NOT REVIEWED"
    assert "approve" not in view.controls and "Approve" not in render(view)


def test_at_the_cap_refine_goes_away_but_approve_and_discard_stay():
    orchestrator, refiner, run, _ = first_view()
    for _ in range(MAX_REFINEMENT_ROUNDS):
        refiner.refine(run.session, "Shorter")
    view = build_view(run.session)

    assert view.refinements_left == 0
    assert view.controls == ("approve", "discard")
    assert "limit" in render(view).lower() and "Refine" not in render(view).replace("Refinement limit", "")


def test_an_approved_ticket_waiting_on_mcp_offers_retry_not_approve():
    _, _, run, _ = first_view()
    run.session.approval.status = "approved"
    run.session.approval.draft_hash = "a" * 64
    view = build_view(run.session, run=run)
    assert view.controls == ("refine", "retry", "discard")
    assert "Retry" in render(view) and "[a] Approve" not in render(view)
    assert not view.closed


def test_a_fully_written_approval_closes_the_ticket():
    _, _, run, _ = first_view()
    run.session.approval.status = "approved"
    run.session.approval.draft_hash = "a" * 64
    run.session.approval.mcp_tools_called = ("append_to_google_doc", "create_email_draft")
    view = build_view(run.session)
    assert view.closed and view.controls == ()
    assert "closed" in render(view).lower()


def test_a_discarded_ticket_has_no_controls():
    orchestrator, refiner, run, _ = first_view()
    refiner.discard(run.session)
    view = build_view(run.session)
    assert view.controls == () and "closed" in render(view).lower()


def test_the_agents_shown_are_the_ones_that_fired_last():
    orchestrator, refiner, run, _ = first_view()
    out = refiner.refine(run.session, "Check if this is the fee issue from last week")
    view = build_view(run.session, run=out.run)
    assert view.agents_fired == ("pattern", "orchestrator")
    # Rebuilt later from the session alone, it still says the same.
    assert build_view(run.session).agents_fired == ("pattern", "orchestrator")


# ---------------------------------------------------------------------------
# No raw text
# ---------------------------------------------------------------------------


def test_the_view_shows_the_redacted_complaint_only():
    _, _, run, view = first_view("my PAN is ABCDE1234F, what is the exit load of the HDFC Large Cap fund?")
    shown = render(view) + repr(view)
    assert "ABCDE1234F" not in shown and "[PAN]" in shown


def test_a_view_is_immutable():
    _, _, _, view = first_view()
    with pytest.raises(Exception):
        view.controls = ("approve",)  # type: ignore[misc]
