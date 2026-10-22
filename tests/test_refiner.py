"""Phase 8: a refinement re-runs only the affected agents, counts its rounds and logs no words."""

from __future__ import annotations

import json

import pytest

from agents.stubs import stub_compliance, stub_facts, stub_pattern
from constants import MAX_REFINEMENT_ROUNDS, SCHEME_SOURCE_URLS
from logging_allowlist import AllowListLogger
from orchestrator.dispatch import Orchestrator
from orchestrator.drafter import DraftOutcome, Drafter, DraftRequest, build_prompt
from orchestrator.graph import AgentSet
from refinement.refiner import Refiner
from refinement.router import RefinementRouter
from reviewer.gate import Reviewer
from schemas import ReplyDraft

QUESTION = "What is the exit load of the HDFC Large Cap fund?"
URL = SCHEME_SOURCE_URLS["large_cap"]
FACT = "HDFC Large Cap Fund Direct Growth has an exit load of 1% if redeemed within 1 year."
SOURCE = f"Source: {URL} (last checked 28-Jun-2026)."


def draft(text: str) -> ReplyDraft:
    return ReplyDraft(text=text, kind="answer", citation=URL, last_checked="28-Jun-2026")


GOOD = draft(f"{FACT} {SOURCE}")
ADVICE = draft(f"{FACT} You should buy more units. {SOURCE}")


class RecordingDrafter(Drafter):
    """The real template drafter. It records the style of every call and every prompt."""

    def __init__(self) -> None:
        super().__init__(writer=self)
        self.styles: list[str] = []
        self.prompts: list[str] = []

    def write(self, request: DraftRequest) -> str:  # the model stand-in: hands the template back
        self.prompts.append(build_prompt(request))
        return request.template

    def draft(self, disposition, facts, *, style="standard", rework=None) -> DraftOutcome:
        self.styles.append(style)
        return super().draft(disposition, facts, style=style, rework=rework)


class ScriptedDrafter:
    def __init__(self, *drafts: ReplyDraft) -> None:
        self.queue = list(drafts)
        self.styles: list[str] = []

    def draft(self, disposition, facts, *, style="standard", rework=None) -> DraftOutcome:
        self.styles.append(style)
        assert self.queue, "the drafter was asked for more drafts than expected"
        return DraftOutcome(self.queue.pop(0))


class Setup:
    def __init__(self, drafter=None, *, router=None, reviewer=True, max_feedback_chars=1000) -> None:
        self.records: list[dict] = []
        self.logger = AllowListLogger(sink=self.records.append)
        self.drafter = drafter or RecordingDrafter()
        self.calls: dict[str, list[dict]] = {"facts": [], "pattern": [], "compliance": []}
        self.facts_scenario = "covered"

        def counted(name, make):
            def run(*args, **kwargs):
                self.calls[name].append(kwargs)
                return make()

            return run

        agents = AgentSet(
            facts=counted("facts", lambda: stub_facts(scenario=self.facts_scenario)),
            pattern=counted("pattern", lambda: stub_pattern(scenario="one_off")),
            compliance=counted("compliance", stub_compliance),
        )
        self.orchestrator = Orchestrator(
            agents,
            self.drafter,
            logger=self.logger,
            reviewer=Reviewer(logger=self.logger) if reviewer else None,
        )
        self.refiner = Refiner(
            self.orchestrator,
            router or RefinementRouter(),
            logger=self.logger,
            max_feedback_chars=max_feedback_chars,
        )
        self.first = self.orchestrator.submit(QUESTION)
        self.session = self.first.session

    def counts(self) -> tuple[int, int, int]:
        return tuple(len(self.calls[n]) for n in ("facts", "pattern", "compliance"))  # type: ignore[return-value]

    def events(self, name):
        return [r for r in self.records if r["event"] == name]

    def everything_seen(self) -> str:
        prompts = getattr(self.drafter, "prompts", [])
        return json.dumps(self.records, default=str) + repr(self.session) + self.session.model_dump_json() + "\n".join(prompts)


# ---------------------------------------------------------------------------
# The three documented cases
# ---------------------------------------------------------------------------


def test_shorter_fires_only_the_drafter_and_reuses_all_three_results():
    setup = Setup()
    out = setup.refiner.refine(setup.session, "Shorter")

    assert out.status == "done"
    assert setup.counts() == (1, 1, 1)  # nobody ran again
    assert out.run.agents_fired == ("orchestrator",)
    assert out.run.agents_cached == ("facts", "pattern", "compliance")
    assert setup.drafter.styles == ["standard", "shorter"]
    assert setup.session.refinement.style == "shorter"


def test_a_different_scheme_fires_facts_and_the_drafter_with_pattern_and_compliance_cached():
    setup = Setup()
    out = setup.refiner.refine(setup.session, "They're asking about the Mid Cap fund, not this one")

    assert setup.counts() == (2, 1, 1)
    assert setup.calls["facts"][1]["scheme_override"] == "mid_cap"
    assert out.run.agents_fired == ("facts", "orchestrator")
    assert out.run.agents_cached == ("pattern", "compliance")
    assert set(out.run.traces) == {"facts"}


def test_a_different_scheme_with_no_name_still_fires_facts_and_asks_for_the_name():
    setup = Setup()
    out = setup.refiner.refine(setup.session, "They're asking about a different scheme")

    assert setup.counts() == (2, 1, 1)
    assert setup.calls["facts"][1]["scheme_override"] is None
    assert out.run.agents_fired == ("facts", "orchestrator")
    assert out.message and "name" in out.message.lower()


def test_the_fee_issue_from_last_week_fires_pattern_with_the_shifted_window():
    setup = Setup()
    out = setup.refiner.refine(setup.session, "Check if this is the fee issue from last week")

    assert setup.counts() == (1, 2, 1)
    second = setup.calls["pattern"][1]
    assert second["theme_hint"] == "fees_brokerage" and second["window_shift_weeks"] == 1
    assert out.run.agents_fired == ("pattern", "orchestrator")
    assert out.run.agents_cached == ("facts", "compliance")


def test_two_affected_agents_run_in_the_same_graph_step():
    setup = Setup()
    out = setup.refiner.refine(setup.session, "It is the Mid Cap fund, and check the fee issue from last week")

    assert setup.counts() == (2, 2, 1)
    assert set(out.run.traces) == {"facts", "pattern"}  # one run of the graph, two nodes
    assert out.run.agents_fired == ("facts", "pattern", "orchestrator")
    assert out.run.agents_cached == ("compliance",)


def test_a_compliance_concern_fires_the_compliance_check_only():
    setup = Setup()
    out = setup.refiner.refine(setup.session, "this sounds like investment advice")
    assert setup.counts() == (1, 1, 2)
    assert out.run.agents_fired == ("compliance", "orchestrator")


def test_ambiguous_feedback_re_drafts_only_and_says_so():
    setup = Setup()
    out = setup.refiner.refine(setup.session, "hmm")

    assert setup.counts() == (1, 1, 1)
    assert out.run.agents_fired == ("orchestrator",)
    assert out.message and "rephrase" in out.message.lower()
    assert setup.events("refinement")[0]["category"] == "unclear"


# ---------------------------------------------------------------------------
# Downstream always re-runs
# ---------------------------------------------------------------------------


def test_a_new_draft_is_merged_again_and_reviewed_again():
    setup = Setup()
    out = setup.refiner.refine(setup.session, "Shorter")

    assert out.run.review is not None and out.run.review.verdict == "PASS"
    assert len(setup.events("reviewer")) == 2  # once for the first draft, once for this one
    assert len(setup.events("disposition")) == 2
    assert setup.session.draft == out.run.outcome.draft is not None
    assert setup.session.reviewer.last_verdict == "PASS"


def test_every_refinement_draft_gets_its_own_one_round_rework():
    # 1: first draft passes. 2: refinement draft fails, rework passes. 3: next refinement
    # draft fails and so does its rework, so it is flagged. 4: the one after is clean again.
    drafter = ScriptedDrafter(GOOD, ADVICE, GOOD, ADVICE, ADVICE, GOOD)
    setup = Setup(drafter)

    setup.refiner.refine(setup.session, "Shorter")
    assert (setup.session.reviewer.rounds_used, setup.session.reviewer.flagged) == (2, False)

    setup.refiner.refine(setup.session, "Shorter")
    assert (setup.session.reviewer.rounds_used, setup.session.reviewer.flagged) == (2, True)
    assert len(drafter.styles) == 5  # never a third draft for one refinement

    setup.refiner.refine(setup.session, "Shorter")
    assert (setup.session.reviewer.rounds_used, setup.session.reviewer.flagged) == (1, False)
    assert setup.session.refinement.rounds_used == 3  # reworks do not use refinement rounds


def test_a_refinement_that_hits_an_agent_failure_holds_with_no_draft_and_still_counts():
    setup = Setup()
    setup.facts_scenario = "error"
    out = setup.refiner.refine(setup.session, "They're asking about the Mid Cap fund")

    assert out.status == "done"
    assert setup.session.disposition == "HOLD_AGENT_FAILURE"
    assert setup.session.draft is None
    assert setup.session.refinement.rounds_used == 1


# ---------------------------------------------------------------------------
# Earlier choices stay
# ---------------------------------------------------------------------------


def test_a_later_refinement_does_not_undo_an_earlier_one():
    setup = Setup()
    setup.refiner.refine(setup.session, "Shorter")
    setup.refiner.refine(setup.session, "They're asking about the Mid Cap fund")
    assert setup.drafter.styles == ["standard", "shorter", "shorter"]
    assert setup.session.refinement.scheme_override == "mid_cap"

    setup.refiner.refine(setup.session, "check if this is the fee issue from last week")
    setup.refiner.refine(setup.session, "what about two weeks ago")
    last = setup.calls["pattern"][-1]
    assert last["theme_hint"] == "fees_brokerage" and last["window_shift_weeks"] == 2
    assert setup.session.refinement.window_shift_weeks == 2


def test_the_state_records_the_category_and_the_agents_for_each_round():
    setup = Setup()
    setup.refiner.refine(setup.session, "Shorter")
    setup.refiner.refine(setup.session, "Check if this is the fee issue from last week")

    state = setup.session.refinement
    assert state.rounds_used == 2
    assert [(h.category, h.agents_fired, h.agents_cached) for h in state.history] == [
        ("style", ("orchestrator",), ("facts", "pattern", "compliance")),
        ("pattern_check", ("pattern", "orchestrator"), ("facts", "compliance")),
    ]


# ---------------------------------------------------------------------------
# The cap
# ---------------------------------------------------------------------------


def test_the_sixth_refinement_is_refused_and_runs_nothing():
    setup = Setup()
    for _ in range(MAX_REFINEMENT_ROUNDS):
        assert setup.refiner.refine(setup.session, "Shorter").status == "done"
    counts, draft_before, events = setup.counts(), setup.session.draft, len(setup.records)

    out = setup.refiner.refine(setup.session, "They're asking about the Mid Cap fund")

    assert out.status == "refused" and out.refusal == "REFINEMENT_CAP"
    assert out.run is None and out.rounds_left == 0
    assert "limit" in out.message.lower() and "approve" in out.message.lower() and "discard" in out.message.lower()
    assert setup.counts() == counts
    assert setup.session.draft == draft_before
    assert setup.session.refinement.rounds_used == MAX_REFINEMENT_ROUNDS
    assert len(setup.records) == events  # a refusal writes no event
    assert setup.session.approval.status == "pending"  # approve and discard are still open


def test_the_rounds_left_are_reported():
    setup = Setup()
    assert setup.refiner.refine(setup.session, "Shorter").rounds_left == 4
    assert setup.refiner.refine(setup.session, "Shorter").rounds_left == 3


def test_a_lower_cap_can_be_set_but_never_a_higher_one():
    setup = Setup()
    with pytest.raises(ValueError):
        Refiner(setup.orchestrator, RefinementRouter(), max_rounds=MAX_REFINEMENT_ROUNDS + 1)
    small = Refiner(setup.orchestrator, RefinementRouter(), max_rounds=1)
    assert small.refine(setup.session, "Shorter").status == "done"
    assert small.refine(setup.session, "Shorter").refusal == "REFINEMENT_CAP"


# ---------------------------------------------------------------------------
# Unusable feedback and a closed ticket
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("feedback", ["", "   ", "!!!", None, 7])
def test_unusable_feedback_is_refused_without_using_a_round(feedback):
    setup = Setup()
    out = setup.refiner.refine(setup.session, feedback)

    assert out.status == "refused" and out.refusal == "FEEDBACK_UNUSABLE"
    assert setup.session.refinement.rounds_used == 0
    assert setup.counts() == (1, 1, 1) and setup.events("refinement") == []
    assert out.rounds_left == MAX_REFINEMENT_ROUNDS


def test_feedback_over_the_length_limit_is_refused_not_cut():
    setup = Setup(max_feedback_chars=40)
    out = setup.refiner.refine(setup.session, "shorter " * 20)
    assert out.refusal == "FEEDBACK_UNUSABLE"
    assert setup.session.refinement.rounds_used == 0


def test_a_refinement_after_approval_voids_the_approval():
    setup = Setup()
    setup.session.approval.status = "approved"
    setup.session.approval.draft_hash = "a" * 64
    setup.session.approval.mcp_tools_called = ("append_to_google_doc",)

    out = setup.refiner.refine(setup.session, "Shorter")
    assert out.status == "done"
    assert setup.session.approval.status == "pending"
    assert setup.session.approval.draft_hash is None
    assert setup.session.approval.mcp_tools_called == ()


def test_a_discarded_ticket_takes_no_more_refinements():
    setup = Setup()
    setup.refiner.discard(setup.session)

    out = setup.refiner.refine(setup.session, "Shorter")
    assert out.status == "refused" and out.refusal == "TICKET_CLOSED"
    assert setup.session.approval.status == "rejected"
    assert setup.counts() == (1, 1, 1)


# ---------------------------------------------------------------------------
# No feedback text anywhere
# ---------------------------------------------------------------------------


def test_the_log_shows_the_agents_and_the_category_and_never_the_feedback():
    setup = Setup()
    setup.refiner.refine(setup.session, "Shorter, zebra quartz marmalade")
    setup.refiner.refine(setup.session, "Check if this is the fee issue from last week")

    events = setup.events("refinement")
    assert [(e["round"], e["category"], e["agents_fired"], e["agents_cached"]) for e in events] == [
        (1, "style", ["orchestrator"], ["facts", "pattern", "compliance"]),
        (2, "pattern_check", ["pattern", "orchestrator"], ["facts", "compliance"]),
    ]
    assert all(e["request_id"] == setup.session.request_id for e in events)
    assert setup.logger.dropped_fields == [] and setup.logger.dropped_events == []
    assert "zebra" not in setup.everything_seen()


def test_pii_typed_into_feedback_is_redacted_before_the_router_sees_it():
    seen: list[str] = []

    class SpyRouter(RefinementRouter):
        def route(self, redacted_feedback):
            seen.append(redacted_feedback)
            return super().route(redacted_feedback)

    setup = Setup(router=SpyRouter())
    out = setup.refiner.refine(setup.session, "shorter please, my PAN is ABCDE1234F zebra")

    assert out.status == "done"
    assert len(seen) == 1 and "ABCDE1234F" not in seen[0] and "[PAN]" in seen[0]
    assert "ABCDE1234F" not in setup.everything_seen() and "zebra" not in setup.everything_seen()
    assert "ABCDE1234F" not in repr(out) and "zebra" not in repr(out)


def test_the_refinement_result_holds_no_feedback_text():
    setup = Setup()
    out = setup.refiner.refine(setup.session, "Shorter, zebra quartz marmalade")
    assert "zebra" not in repr(out) + str(out.plan) + str(out.message)
