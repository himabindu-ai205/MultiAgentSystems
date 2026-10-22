"""Phase 7: the Orchestrator sends a failed draft back once, then flags it (C6, REV-13 to REV-21)."""

from __future__ import annotations

import json

from agents.stubs import stub_compliance, stub_facts, stub_pattern
from constants import SCHEME_SOURCE_URLS
from logging_allowlist import AllowListLogger
from orchestrator.dispatch import Orchestrator
from orchestrator.drafter import DraftOutcome, Drafter, DraftRequest
from orchestrator.graph import AgentSet
from reviewer.gate import Reviewer
from schemas import ReplyDraft, ReviewerResult, ReviewHandoff

QUESTION = "What is the exit load of the HDFC Large Cap fund?"
URL = SCHEME_SOURCE_URLS["large_cap"]
FACT = "HDFC Large Cap Fund Direct Growth has an exit load of 1% if redeemed within 1 year."
SOURCE = f"Source: {URL} (last checked 28-Jun-2026)."


def draft(text: str) -> ReplyDraft:
    return ReplyDraft(text=text, kind="answer", citation=URL, last_checked="28-Jun-2026")


GOOD = draft(f"{FACT} {SOURCE}")
UNCITED = draft(f"{FACT} (last checked 28-Jun-2026).")  # a fact with no link
ADVICE = draft(f"{FACT} You should buy more units. {SOURCE}")  # advice wording
WRONG_LINK = draft(f"{FACT} Source: {SCHEME_SOURCE_URLS['mid_cap']} (last checked 28-Jun-2026).")


class ScriptedDrafter:
    """Hands out prepared drafts in order and records every call. A call too many fails the test."""

    def __init__(self, *drafts: ReplyDraft | None) -> None:
        self.queue = list(drafts)
        self.calls: list[dict] = []

    def draft(self, disposition, facts, *, style="standard", rework=None) -> DraftOutcome:
        self.calls.append({"disposition": disposition, "style": style, "rework": rework})
        assert self.queue, "the drafter was asked for more drafts than the cap allows"
        item = self.queue.pop(0)
        if item is None:
            return DraftOutcome(None, reason_code="DRAFT_BAD_INPUT")
        return DraftOutcome(item)


class Setup:
    def __init__(self, *drafts, reviewer=None) -> None:
        self.records: list[dict] = []
        self.logger = AllowListLogger(sink=self.records.append)
        self.drafter = ScriptedDrafter(*drafts)
        self.agent_calls = {"facts": 0, "pattern": 0, "compliance": 0}

        def counted(name, make):
            def run(*args, **kwargs):
                self.agent_calls[name] += 1
                return make()

            return run

        agents = AgentSet(
            facts=counted("facts", stub_facts),
            pattern=counted("pattern", lambda: stub_pattern(scenario="one_off")),
            compliance=counted("compliance", stub_compliance),
        )
        self.reviewer = reviewer or Reviewer(logger=self.logger)
        self.orchestrator = Orchestrator(agents, self.drafter, logger=self.logger, reviewer=self.reviewer)

    def submit(self):
        return self.orchestrator.submit(QUESTION)

    def events(self, name):
        return [r for r in self.records if r["event"] == name]


# ---------------------------------------------------------------------------
# The first look
# ---------------------------------------------------------------------------


def test_a_clean_draft_passes_on_the_first_round_and_is_not_redrafted():
    setup = Setup(GOOD)
    out = setup.submit()

    assert len(setup.drafter.calls) == 1 and setup.drafter.calls[0]["rework"] is None
    assert out.review.verdict == "PASS" and out.review.round == 1
    state = out.session.reviewer
    assert (state.rounds_used, state.last_verdict, state.flagged) == (1, "PASS", False)
    assert out.session.draft == GOOD


# ---------------------------------------------------------------------------
# One rework
# ---------------------------------------------------------------------------


def test_a_failed_draft_goes_back_once_with_the_failure_named_and_the_rework_passes():
    # REV-13
    setup = Setup(UNCITED, GOOD)
    out = setup.submit()

    first, second = setup.drafter.calls
    assert first["rework"] is None
    assert "source link" in second["rework"]
    assert out.session.draft == GOOD
    assert out.review.verdict == "PASS" and out.review.round == 2 and out.review.flagged is False
    state = out.session.reviewer
    assert (state.rounds_used, state.last_verdict, state.flagged) == (2, "PASS", False)


def test_the_rework_asks_the_drafter_again_and_does_not_rerun_any_agent():
    setup = Setup(UNCITED, GOOD)
    setup.submit()

    assert setup.agent_calls == {"facts": 1, "pattern": 1, "compliance": 1}
    assert len(setup.drafter.calls) == 2


def test_the_rework_keeps_the_same_disposition_and_style():
    setup = Setup(UNCITED, GOOD)
    out = setup.submit()

    assert [c["disposition"] for c in setup.drafter.calls] == ["SEND_READY", "SEND_READY"]
    assert out.session.disposition == "SEND_READY"


def test_the_reviewer_sees_round_one_then_round_two():
    seen: list[int] = []

    def reviewer(handoff: ReviewHandoff) -> ReviewerResult:
        seen.append(handoff.round)
        return Reviewer()(handoff)

    Setup(UNCITED, GOOD, reviewer=reviewer).submit()
    assert seen == [1, 2]


def test_the_second_review_judges_the_reworked_draft_not_the_first():
    judged: list[ReplyDraft] = []

    def reviewer(handoff: ReviewHandoff) -> ReviewerResult:
        judged.append(handoff.draft)
        return Reviewer()(handoff)

    Setup(UNCITED, GOOD, reviewer=reviewer).submit()
    assert judged == [UNCITED, GOOD]


# ---------------------------------------------------------------------------
# The cap
# ---------------------------------------------------------------------------


def test_a_draft_that_fails_twice_is_flagged_and_never_drafted_a_third_time():
    # REV-14, and REV-16 when the rework repeats the failure.
    setup = Setup(UNCITED, UNCITED)
    out = setup.submit()

    assert len(setup.drafter.calls) == 2
    assert out.review.verdict == "FAIL" and out.review.round == 2 and out.review.flagged is True
    state = out.session.reviewer
    assert (state.rounds_used, state.last_verdict, state.flagged) == (2, "FAIL", True)
    # The flagged draft is kept, so the human can see it with its flag.
    assert out.session.draft == UNCITED


def test_a_rework_that_fixes_one_failure_but_adds_another_is_flagged():
    # REV-15
    setup = Setup(UNCITED, ADVICE)
    out = setup.submit()

    assert out.session.reviewer.flagged is True
    assert "ADVICE_LANGUAGE" in out.review.failure_types
    assert len(setup.drafter.calls) == 2


def test_a_reviewer_that_forgets_to_flag_the_second_failure_is_overruled():
    # The approve control depends on the flag, so the Orchestrator does not leave it to the Reviewer.
    def lax(handoff: ReviewHandoff) -> ReviewerResult:
        return ReviewerResult(verdict="FAIL", round=handoff.round, failure_types=("UNCITED_CLAIM",), instruction="Fix it.")

    out = Setup(UNCITED, UNCITED, reviewer=lax).submit()

    assert out.session.reviewer.flagged is True
    assert out.review.flagged is True and out.review.round == 2


def test_a_reviewer_that_passes_every_time_is_looked_at_once():
    calls: list[int] = []

    def easy(handoff: ReviewHandoff) -> ReviewerResult:
        calls.append(handoff.round)
        return ReviewerResult(verdict="PASS", round=handoff.round)

    Setup(GOOD, reviewer=easy).submit()
    assert calls == [1]


# ---------------------------------------------------------------------------
# When the rework cannot draft
# ---------------------------------------------------------------------------


def test_a_rework_that_cannot_draft_is_a_failure_to_the_human_not_a_pass():
    setup = Setup(UNCITED, None)
    out = setup.submit()

    assert out.session.draft is None
    assert out.session.disposition == "HOLD_AGENT_FAILURE"
    assert out.review.verdict == "FAIL" and out.review.round == 1
    assert out.session.reviewer.last_verdict == "FAIL"
    assert out.session.reviewer.flagged is False  # there is no draft to flag


# ---------------------------------------------------------------------------
# Each draft has its own cap (REV-21)
# ---------------------------------------------------------------------------


def test_a_refinement_draft_gets_its_own_one_round_limit():
    setup = Setup(UNCITED, UNCITED, GOOD)
    first = setup.submit()
    assert first.session.reviewer.flagged is True

    second = setup.orchestrator.rerun(first.session, [], style="shorter")

    assert second.session.draft == GOOD
    state = second.session.reviewer
    assert (state.rounds_used, state.last_verdict, state.flagged) == (1, "PASS", False)
    assert second.review.round == 1


def test_a_refinement_draft_that_fails_gets_its_own_rework_and_can_be_flagged_again():
    setup = Setup(UNCITED, UNCITED, UNCITED, ADVICE)
    first = setup.submit()
    second = setup.orchestrator.rerun(first.session, [], style="shorter")

    assert len(setup.drafter.calls) == 4  # two for each draft, never more
    assert second.session.reviewer.flagged is True
    assert second.session.reviewer.rounds_used == 2
    assert setup.drafter.calls[3]["style"] == "shorter"
    assert "advice" not in (setup.drafter.calls[2]["rework"] or "").lower()


def test_a_draft_that_was_flagged_before_can_pass_after_a_new_run_of_one_agent():
    setup = Setup(WRONG_LINK, WRONG_LINK, GOOD)
    first = setup.submit()
    second = setup.orchestrator.rerun(first.session, ["facts"])

    assert second.session.reviewer.flagged is False
    assert second.review.verdict == "PASS"


# ---------------------------------------------------------------------------
# Other paths
# ---------------------------------------------------------------------------


def test_no_review_and_no_rework_without_a_draft():
    setup = Setup()
    setup.orchestrator = Orchestrator(
        AgentSet(
            facts=lambda t, **k: stub_facts(scenario="error"),
            pattern=lambda t, **k: stub_pattern(),
            compliance=lambda t: stub_compliance(),
        ),
        setup.drafter,
        logger=setup.logger,
        reviewer=setup.reviewer,
    )
    out = setup.submit()

    assert out.session.disposition == "HOLD_AGENT_FAILURE"
    assert setup.drafter.calls == [] and setup.events("reviewer") == []
    assert out.review is None
    assert out.session.reviewer.rounds_used == 0


def test_without_a_reviewer_nothing_is_reviewed_or_reworked():
    drafter = ScriptedDrafter(UNCITED)
    plain = Orchestrator(
        AgentSet(
            facts=lambda t, **k: stub_facts(),
            pattern=lambda t, **k: stub_pattern(),
            compliance=lambda t: stub_compliance(),
        ),
        drafter,
    )
    out = plain.submit(QUESTION)

    assert out.review is None
    assert out.session.draft == UNCITED
    assert out.session.reviewer.rounds_used == 0
    assert len(drafter.calls) == 1


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def test_each_review_round_is_logged_and_the_disposition_only_once():
    setup = Setup(UNCITED, GOOD)
    out = setup.submit()

    reviews = setup.events("reviewer")
    assert [(r["round"], r["verdict"]) for r in reviews] == [(1, "FAIL"), (2, "PASS")]
    assert reviews[0]["failure_types"] == ["UNCITED_CLAIM"]
    assert all(r["request_id"] == out.session.request_id for r in reviews)
    assert len(setup.events("disposition")) == 1
    assert setup.logger.dropped_fields == [] and setup.logger.dropped_events == []


def test_no_draft_text_reaches_the_log_during_a_rework():
    setup = Setup(UNCITED, GOOD)
    setup.submit()
    assert FACT not in json.dumps(setup.records)


# ---------------------------------------------------------------------------
# With the real drafter
# ---------------------------------------------------------------------------


class BadWriter:
    """A model that ignores the rules."""

    def __init__(self) -> None:
        self.requests: list[DraftRequest] = []

    def write(self, request: DraftRequest) -> str:
        self.requests.append(request)
        return f"{FACT} You should buy more units. {SOURCE}"


def test_bad_model_wording_is_stopped_before_the_reviewer_and_the_template_passes():
    writer = BadWriter()
    setup = Setup()
    setup.orchestrator = Orchestrator(
        AgentSet(
            facts=lambda t, **k: stub_facts(),
            pattern=lambda t, **k: stub_pattern(),
            compliance=lambda t: stub_compliance(),
        ),
        Drafter(writer=writer),
        logger=setup.logger,
        reviewer=setup.reviewer,
    )
    out = setup.submit()

    assert out.session.draft.text == f"{FACT} {SOURCE} If this doesn't match what you see, reply and we will look into it."
    assert out.review.verdict == "PASS" and out.review.round == 1
    assert len(writer.requests) == 1