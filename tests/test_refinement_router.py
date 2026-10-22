"""Phase 8: the refinement router decides which agents re-run and with what parameters."""

from __future__ import annotations

import pytest

from constants import REFINEMENT_CATEGORIES
from orchestrator.drafter import SCHEME_LABELS
from orchestrator.graph import NODE_NAMES
from refinement.router import FEEDBACK_LABELS, RefinementPlan, RefinementRouter


@pytest.fixture(scope="module")
def router() -> RefinementRouter:
    return RefinementRouter()


class FakeClassifier:
    def __init__(self, label="unclear", *, exc: Exception | None = None) -> None:
        self.label, self.exc = label, exc
        self.seen: list[str] = []

    def classify(self, text: str) -> str:
        self.seen.append(text)
        if self.exc:
            raise self.exc
        return self.label


# ---------------------------------------------------------------------------
# The three documented cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "feedback",
    [
        "Shorter",
        "make it shorter",
        "SHORTER!!",
        "this is too long, please trim it",
        "can you be more concise",
        "shorten the reply",
    ],
)
def test_shorter_re_drafts_only(router, feedback):
    plan = router.route(feedback)
    assert plan.category == "style"
    assert plan.nodes == ()
    assert plan.style == "shorter"
    assert (plan.scheme_override, plan.theme_hint, plan.window_shift_weeks) == (None, None, None)
    assert plan.notice is None


def test_a_different_scheme_with_no_name_runs_facts_and_asks_for_the_name(router):
    plan = router.route("They're asking about a different scheme")
    assert plan.category == "scheme_change"
    assert plan.nodes == ("facts",)
    assert plan.scheme_override is None
    assert plan.style is None
    assert plan.notice and "name" in plan.notice.lower()


@pytest.mark.parametrize("scheme_id,label", sorted(SCHEME_LABELS.items()))
def test_a_named_scheme_becomes_the_override(router, scheme_id, label):
    plan = router.route(f"They are asking about the {label}, not this one")
    assert plan.category == "scheme_change"
    assert plan.nodes == ("facts",)
    assert plan.scheme_override == scheme_id
    assert plan.notice is None


def test_the_fee_issue_from_last_week_runs_pattern_with_a_shifted_window(router):
    plan = router.route("Check if this is the fee issue from last week")
    assert plan.category == "pattern_check"
    assert plan.nodes == ("pattern",)
    assert plan.theme_hint == "fees_brokerage"
    assert plan.window_shift_weeks == 1
    assert plan.scheme_override is None
    assert plan.notice is None


@pytest.mark.parametrize(
    "feedback,theme,weeks",
    [
        ("is this the same withdrawal problem from two weeks ago", "money_movement", 2),
        ("check the app crash issue from 3 weeks ago", "reliability", 3),
        ("was there a chart issue a month ago", "trading_charts", 4),
        ("is this a recurring brokerage charges issue", "fees_brokerage", None),
        ("check the previous 7 days", None, 1),
        ("look at the week before", None, 1),
    ],
)
def test_pattern_feedback_picks_the_theme_and_the_window_from_the_words(router, feedback, theme, weeks):
    plan = router.route(feedback)
    assert plan.category == "pattern_check"
    assert plan.nodes == ("pattern",)
    assert plan.theme_hint == theme
    assert plan.window_shift_weeks == weeks


def test_the_window_shift_has_an_upper_bound(router):
    assert router.route("check the fee issue from 100 weeks ago").window_shift_weeks == 52


@pytest.mark.parametrize(
    "feedback",
    ["this sounds like investment advice", "is this reply compliant?", "it reads like a recommendation"],
)
def test_a_compliance_concern_runs_the_compliance_check(router, feedback):
    plan = router.route(feedback)
    assert plan.category == "compliance_concern"
    assert plan.nodes == ("compliance_inbound",)
    assert plan.style is None


# ---------------------------------------------------------------------------
# Combined feedback
# ---------------------------------------------------------------------------


def test_feedback_can_ask_for_a_style_and_an_agent_at_once(router):
    plan = router.route("shorter, and check if it is the fee issue from last week")
    assert plan.category == "pattern_check"  # the agent change names the round
    assert plan.nodes == ("pattern",)
    assert plan.style == "shorter"
    assert plan.window_shift_weeks == 1


def test_two_agents_come_back_in_graph_order_so_they_start_together(router):
    plan = router.route("it is about the Mid Cap fund, and check the fee issue from last week")
    assert plan.nodes == ("facts", "pattern")
    assert plan.category == "scheme_change"
    assert plan.scheme_override == "mid_cap"
    assert plan.theme_hint == "fees_brokerage"
    assert list(plan.nodes) == [n for n in NODE_NAMES if n in plan.nodes]


# ---------------------------------------------------------------------------
# Ambiguous feedback goes to the Orchestrator only, and says so
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "feedback", ["hmm", "make it better", "mention the fee", "the exit load answer is wrong", "ok", "   "]
)
def test_ambiguous_feedback_re_drafts_only_and_tells_the_human_to_rephrase(router, feedback):
    plan = router.route(feedback)
    assert plan.category == "unclear"
    assert plan.nodes == ()
    assert plan.style is None
    assert plan.notice and "rephrase" in plan.notice.lower()


def test_naming_two_schemes_is_ambiguous(router):
    plan = router.route("it is about the Large Cap and the Small Cap fund")
    assert plan.category == "unclear"
    assert plan.nodes == ()
    assert "one scheme" in plan.notice.lower()


def test_a_fund_from_another_company_is_not_a_scheme_change(router):
    plan = router.route("they are asking about the SBI Bluechip fund")
    assert plan.category == "unclear"
    assert plan.nodes == ()
    assert "five" in plan.notice.lower()


# ---------------------------------------------------------------------------
# The optional classifier, for free wording
# ---------------------------------------------------------------------------


def test_the_classifier_is_not_asked_when_a_rule_matches():
    classifier = FakeClassifier("pattern_check")
    plan = RefinementRouter(classifier=classifier).route("shorter")
    assert plan.category == "style"
    assert classifier.seen == []


@pytest.mark.parametrize(
    "label,category,nodes,style",
    [
        ("shorter", "style", (), "shorter"),
        ("scheme_change", "scheme_change", ("facts",), None),
        ("pattern_check", "pattern_check", ("pattern",), None),
        ("compliance_concern", "compliance_concern", ("compliance_inbound",), None),
        ("unclear", "unclear", (), None),
    ],
)
def test_the_classifier_label_picks_the_agents_when_no_rule_matches(label, category, nodes, style):
    classifier = FakeClassifier(label)
    plan = RefinementRouter(classifier=classifier).route("could you take another look from a new angle")
    assert (plan.category, plan.nodes, plan.style) == (category, nodes, style)
    assert classifier.seen == ["could you take another look from a new angle"]


@pytest.mark.parametrize("label", ["", "style", "everything", "facts, pattern", "SHORTER", None, 7])
def test_any_label_outside_the_list_is_unclear(label):
    plan = RefinementRouter(classifier=FakeClassifier(label)).route("take another look from a new angle")
    assert plan.category == "unclear" and plan.nodes == ()


def test_a_classifier_that_raises_is_unclear_not_a_crash():
    plan = RefinementRouter(classifier=FakeClassifier(exc=RuntimeError("boom"))).route("a new angle please")
    assert plan.category == "unclear" and plan.nodes == ()


def test_the_classifier_picks_the_agents_but_never_the_parameters():
    # The label says scheme_change. The scheme can only come from the rules, which found none.
    plan = RefinementRouter(classifier=FakeClassifier("scheme_change")).route("look at the other product")
    assert plan.nodes == ("facts",)
    assert plan.scheme_override is None


# ---------------------------------------------------------------------------
# What a plan can hold
# ---------------------------------------------------------------------------


def test_every_category_is_a_logged_category(router):
    for feedback in ["shorter", "a different scheme", "fee issue last week", "advice?", "hmm"]:
        assert router.route(feedback).category in REFINEMENT_CATEGORIES


def test_the_classifier_labels_are_the_documented_ones():
    assert FEEDBACK_LABELS == ("shorter", "scheme_change", "pattern_check", "compliance_concern", "unclear")


def test_a_plan_never_holds_the_feedback_words(router):
    feedback = "Check if this is the fee issue from last week, zebra quartz marmalade"
    plan = router.route(feedback)
    shown = repr(plan) + str(plan)
    assert "zebra" not in shown and "marmalade" not in shown
    assert isinstance(plan, RefinementPlan)


def test_a_plan_is_immutable(router):
    plan = router.route("shorter")
    with pytest.raises(Exception):
        plan.nodes = ("facts",)  # type: ignore[misc]
