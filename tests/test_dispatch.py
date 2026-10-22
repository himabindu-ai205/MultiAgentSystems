"""Phase 6: the Orchestrator's dispatch. Redact first, run the graph, merge, draft, hand on."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from agents.compliance import ComplianceAgent
from agents.facts import FactsAgent
from agents.pattern import PatternAgent
from agents.stubs import stub_compliance, stub_facts, stub_pattern
from constants import SCHEME_SOURCE_URLS
from ingress.redactor import IngressError
from logging_allowlist import AllowListLogger
from orchestrator.dispatch import Orchestrator, ReviewHandoff
from orchestrator.drafter import Drafter, DraftRequest, build_prompt
from orchestrator.graph import AgentSet, NodeParams, start_spread_ms
from schemas import ReviewerResult

QUESTION = "What is the exit load of the HDFC Large Cap fund?"
FIXTURES = Path(__file__).parent / "fixtures"


class Spy:
    """Stub agents that record exactly what they were called with."""

    def __init__(self, *, facts="covered", pattern="one_off", compliance="clean") -> None:
        self.facts_scenario, self.pattern_scenario, self.compliance_scenario = facts, pattern, compliance
        self.calls: dict[str, list[tuple[tuple, dict]]] = {"facts": [], "pattern": [], "compliance": []}
        self.lock = threading.Lock()

    def _note(self, name, args, kwargs):
        with self.lock:
            self.calls[name].append((args, kwargs))

    def count(self, name: str) -> int:
        return len(self.calls[name])

    def agents(self) -> AgentSet:
        def facts(*args, **kwargs):
            self._note("facts", args, kwargs)
            return stub_facts(scenario=self.facts_scenario)

        def pattern(*args, **kwargs):
            self._note("pattern", args, kwargs)
            return stub_pattern(scenario=self.pattern_scenario)

        def compliance(*args, **kwargs):
            self._note("compliance", args, kwargs)
            return stub_compliance(scenario=self.compliance_scenario)

        return AgentSet(facts=facts, pattern=pattern, compliance=compliance)

    def everything_seen(self) -> str:
        return repr(self.calls)


class Capture:
    """Collects log records and the prompts a drafting model would receive."""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self.prompts: list[str] = []
        self.logger = AllowListLogger(sink=self.records.append)

    # DraftWriter
    def write(self, request: DraftRequest) -> str:
        self.prompts.append(build_prompt(request))
        return request.template

    def drafter(self) -> Drafter:
        return Drafter(writer=self)

    def everything_seen(self) -> str:
        return json.dumps(self.records, default=str) + "\n".join(self.prompts)


def orchestrator(spy: Spy, capture: Capture | None = None, **kwargs) -> Orchestrator:
    capture = capture or Capture()
    return Orchestrator(spy.agents(), capture.drafter(), logger=capture.logger, **kwargs)


# ---------------------------------------------------------------------------
# A first run
# ---------------------------------------------------------------------------


def test_a_clean_covered_complaint_is_send_ready_with_a_cited_draft():
    spy, capture = Spy(), Capture()
    out = orchestrator(spy, capture).submit(QUESTION)

    session = out.session
    assert session.disposition == "SEND_READY"
    assert session.draft is not None
    assert session.draft.citation == SCHEME_SOURCE_URLS["large_cap"]
    assert session.routing_note is not None
    assert session.results.facts.status == "covered"
    assert session.results.pattern.signal == "one_off"
    assert session.results.compliance_inbound.clean is True
    assert session.redacted_text == QUESTION
    assert out.outcome.draft == session.draft
    assert set(out.traces) == {"facts", "pattern", "compliance_inbound"}
    assert out.agents_fired == ("facts", "pattern", "compliance", "orchestrator")
    assert out.agents_cached == ()
    assert out.review is None


def test_the_first_run_hands_each_agent_only_the_redacted_text():
    spy = Spy()
    orchestrator(spy).submit(QUESTION)

    assert spy.calls["facts"] == [((QUESTION,), {"scheme_override": None})]
    assert spy.calls["pattern"] == [((QUESTION,), {"theme_hint": None, "window_shift_weeks": 0})]
    assert spy.calls["compliance"] == [((QUESTION,), {})]


def test_an_advice_request_is_a_refusal_and_is_counted():
    out = orchestrator(Spy(compliance="advice_request")).submit("Should I redeem my fund?")

    assert out.session.disposition == "REFUSAL"
    assert out.session.advice_attempts == 1
    assert "Advice requests in this session: 1." in out.session.routing_note.reason


def test_covered_with_a_rising_theme_holds_for_disagreement():
    out = orchestrator(Spy(pattern="rising")).submit(QUESTION)

    assert out.session.disposition == "HOLD_DISAGREEMENT"
    assert out.session.routing_note.disagreement


def test_a_failed_agent_gives_hold_agent_failure_and_no_draft():
    out = orchestrator(Spy(pattern="error")).submit(QUESTION)

    assert out.session.disposition == "HOLD_AGENT_FAILURE"
    assert out.session.draft is None
    assert out.outcome.draft is None
    assert [f.agent for f in out.outcome.failures] == ["pattern"]
    assert out.review is None


def test_a_timed_out_agent_gives_hold_agent_failure_and_the_other_two_still_report():
    release = threading.Event()
    spy = Spy()
    agents = spy.agents()

    def slow_facts(text, **kwargs):
        release.wait(timeout=2)
        return stub_facts()

    slow = AgentSet(facts=slow_facts, pattern=agents.pattern, compliance=agents.compliance)
    capture = Capture()
    try:
        out = Orchestrator(slow, capture.drafter(), logger=capture.logger, timeout_seconds=0.2).submit(QUESTION)
    finally:
        release.set()

    assert out.session.disposition == "HOLD_AGENT_FAILURE"
    assert out.session.draft is None
    assert out.session.results.pattern.status == "ok"
    assert out.traces["facts"].status == "timeout"
    assert spy.count("pattern") == 1 and spy.count("compliance") == 1


# ---------------------------------------------------------------------------
# Redaction first
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["", "   ", "!!! ???", None, 42])
def test_a_failed_redaction_starts_no_agent(raw):
    spy, capture = Spy(), Capture()
    with pytest.raises(IngressError):
        orchestrator(spy, capture).submit(raw)

    assert all(not calls for calls in spy.calls.values())
    assert capture.records == []


def test_pii_is_redacted_before_any_agent_sees_the_text():
    spy = Spy()
    out = orchestrator(spy).submit("My PAN is ABCDE1234F and my number is 9876543210, withdrawal stuck")

    seen = spy.everything_seen()
    assert "ABCDE1234F" not in seen and "9876543210" not in seen
    assert "[PAN]" in seen and "[PHONE]" in seen
    assert out.session.pii_manifest.PAN == 1
    assert out.session.pii_manifest.PHONE == 1
    assert "[PAN]" in out.session.redacted_text


def test_a_caller_supplied_request_id_is_used_everywhere():
    capture = Capture()
    request_id = "8a1e6b6c-3a63-4a0f-9a55-6a2d9d2f6f11"
    out = orchestrator(Spy(), capture).submit(QUESTION, request_id=request_id)

    assert out.session.request_id == request_id
    assert capture.records and all(r["request_id"] == request_id for r in capture.records)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def test_each_agent_is_logged_with_its_start_and_end_times():
    capture = Capture()
    orchestrator(Spy(), capture).submit(QUESTION)

    runs = [r for r in capture.records if r["event"] == "agent_run"]
    assert sorted(r["agent"] for r in runs) == ["compliance", "facts", "pattern"]
    for record in runs:
        assert {"status", "duration_ms", "started_at", "ended_at"} <= set(record)
    assert [r for r in capture.records if r["event"] == "disposition"][0]["value"] == "SEND_READY"
    assert capture.logger.dropped_fields == [] and capture.logger.dropped_events == []


def test_a_timeout_is_logged_as_a_timeout_with_a_fixed_code():
    capture = Capture()
    release = threading.Event()
    spy = Spy()
    agents = spy.agents()
    slow = AgentSet(
        facts=lambda t, **k: release.wait(timeout=2) or stub_facts(),
        pattern=agents.pattern,
        compliance=agents.compliance,
    )
    try:
        Orchestrator(slow, capture.drafter(), logger=capture.logger, timeout_seconds=0.2).submit(QUESTION)
    finally:
        release.set()

    facts_run = [r for r in capture.records if r["event"] == "agent_run" and r["agent"] == "facts"][0]
    assert facts_run["status"] == "timeout"
    assert facts_run["error_code"] == "FACTS_TIMEOUT"
    assert capture.logger.dropped_fields == []


# ---------------------------------------------------------------------------
# Hand-off to the Reviewer
# ---------------------------------------------------------------------------


def test_the_draft_routing_note_and_evidence_go_to_the_reviewer():
    handoffs: list[ReviewHandoff] = []
    verdict = ReviewerResult(verdict="PASS", round=1)

    def reviewer(handoff: ReviewHandoff) -> ReviewerResult:
        handoffs.append(handoff)
        return verdict

    out = orchestrator(Spy(), reviewer=reviewer).submit(QUESTION)

    assert len(handoffs) == 1
    handoff = handoffs[0]
    assert handoff.request_id == out.session.request_id
    assert handoff.draft == out.session.draft
    assert handoff.routing_note == out.session.routing_note
    assert handoff.facts == out.session.results.facts
    assert out.review == verdict


def test_nothing_goes_to_the_reviewer_when_there_is_no_draft():
    calls: list[ReviewHandoff] = []
    out = orchestrator(Spy(facts="error"), reviewer=lambda h: calls.append(h)).submit(QUESTION)

    assert calls == []
    assert out.review is None


# ---------------------------------------------------------------------------
# Running again with only some agents
# ---------------------------------------------------------------------------


def test_a_rerun_starts_only_the_chosen_agent_and_reuses_the_rest():
    spy = Spy()
    orch = orchestrator(spy)
    first = orch.submit(QUESTION)
    cached_pattern = first.session.results.pattern
    cached_compliance = first.session.results.compliance_inbound

    second = orch.rerun(first.session, ["facts"], params=NodeParams(scheme_override="mid_cap"))

    assert spy.count("facts") == 2 and spy.count("pattern") == 1 and spy.count("compliance") == 1
    assert spy.calls["facts"][1] == ((QUESTION,), {"scheme_override": "mid_cap"})
    assert second.session.results.pattern is cached_pattern
    assert second.session.results.compliance_inbound is cached_compliance
    assert set(second.traces) == {"facts"}
    assert second.agents_fired == ("facts", "orchestrator")
    assert second.agents_cached == ("pattern", "compliance")


def test_a_rerun_merges_the_new_result_with_the_cached_ones():
    spy = Spy(pattern="rising")
    orch = orchestrator(spy)
    first = orch.submit(QUESTION)
    assert first.session.disposition == "HOLD_DISAGREEMENT"

    spy.facts_scenario = "not_covered"
    second = orch.rerun(first.session, ["facts"])

    assert second.session.disposition == "NOT_COVERED"
    assert second.session.draft.kind == "not_covered"


def test_a_rerun_of_two_agents_starts_them_together():
    spy = Spy()
    orch = orchestrator(spy)
    first = orch.submit(QUESTION)

    second = orch.rerun(first.session, ["facts", "pattern"])

    assert set(second.traces) == {"facts", "pattern"}
    assert second.agents_fired == ("facts", "pattern", "orchestrator")
    assert second.agents_cached == ("compliance",)


def test_a_style_only_rerun_starts_no_agent_and_redrafts_shorter():
    # A not-covered reply has a closing sentence that "shorter" drops.
    spy = Spy(facts="not_covered")
    orch = orchestrator(spy)
    first = orch.submit(QUESTION)
    first_text = first.session.draft.text  # the session is updated in place by the rerun
    before = {name: spy.count(name) for name in spy.calls}

    second = orch.rerun(first.session, [], style="shorter")

    assert {name: spy.count(name) for name in spy.calls} == before
    assert second.traces == {}
    assert second.agents_fired == ("orchestrator",)
    assert second.agents_cached == ("facts", "pattern", "compliance")
    assert len(second.session.draft.text) < len(first_text)


def test_a_rerun_does_not_count_the_same_advice_request_twice():
    spy = Spy(compliance="advice_request")
    orch = orchestrator(spy)
    first = orch.submit("Should I redeem?")
    second = orch.rerun(first.session, ["compliance_inbound"])

    assert first.session.advice_attempts == 1
    assert second.session.advice_attempts == 1


def test_a_rerun_needs_a_cached_result_for_every_agent_it_skips():
    orch = orchestrator(Spy())
    first = orch.submit(QUESTION)
    first.session.results.pattern = None

    with pytest.raises(ValueError, match="pattern"):
        orch.rerun(first.session, ["facts"])


def test_a_rerun_with_an_unknown_agent_is_rejected():
    orch = orchestrator(Spy())
    first = orch.submit(QUESTION)
    with pytest.raises(ValueError):
        orch.rerun(first.session, ["facts", "nope"])


# ---------------------------------------------------------------------------
# With the real agents
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real(data):
    return {
        "facts": FactsAgent(data.corpus),
        "pattern": PatternAgent(data.themes),
        "compliance": ComplianceAgent(corpus=data.corpus),
        "corpus": data.corpus,
        "themes": data.themes,
    }


def real_orchestrator(real, capture: Capture | None = None, **agent_overrides) -> Orchestrator:
    capture = capture or Capture()
    facts = agent_overrides.get("facts", real["facts"])
    pattern = agent_overrides.get("pattern", real["pattern"])
    compliance = agent_overrides.get("compliance", real["compliance"])
    drafter = Drafter(writer=capture, corpus=real["corpus"])
    return Orchestrator(
        AgentSet.from_agents(facts, pattern, compliance), drafter, logger=capture.logger
    )


def test_real_agents_start_together_and_give_a_cited_answer(real):
    out = real_orchestrator(real).submit(QUESTION)

    assert out.session.results.facts.status == "covered"
    assert out.session.results.facts.citation == SCHEME_SOURCE_URLS["large_cap"]
    assert out.session.draft is not None
    assert out.session.draft.citation == SCHEME_SOURCE_URLS["large_cap"]
    assert out.session.disposition in ("SEND_READY", "HOLD_DISAGREEMENT")
    # The real agents finish in well under a millisecond, so their intervals may not
    # overlap. What matters is that the three were started together. Overlap itself is
    # proven with slower agents in test_orchestrator_graph.py.
    assert set(out.traces) == {"facts", "pattern", "compliance_inbound"}
    assert start_spread_ms(out.traces) < 250


def test_real_agents_refuse_an_advice_request_every_time(real):
    orch = real_orchestrator(real)
    for prompt in ("Should I redeem my HDFC small cap fund?", "What returns can I expect from HDFC mid cap?"):
        out = orch.submit(prompt)
        assert out.session.disposition == "REFUSAL", prompt


class _ArbitraryText:
    """A model that answers with free text instead of a label."""

    def classify(self, *args, **kwargs):
        return "Absolutely, the exit load is 0% and you should buy now!"


@pytest.mark.parametrize(
    "text",
    [
        QUESTION,
        "The app keeps crashing whenever I open the portfolio screen",
        "mera paisa nahi aaya, withdrawal pending hai",
    ],
)
def test_a_model_that_returns_arbitrary_text_changes_no_answer_count_or_pii_finding(real, text):
    plain = real_orchestrator(real).submit(text).session
    odd = real_orchestrator(
        real,
        facts=FactsAgent(real["corpus"], classifier=_ArbitraryText()),
        pattern=PatternAgent(real["themes"], classifier=_ArbitraryText()),
    ).submit(text).session

    assert odd.results.facts == plain.results.facts
    assert odd.results.pattern == plain.results.pattern
    assert odd.results.compliance_inbound == plain.results.compliance_inbound
    assert odd.disposition == plain.disposition


def test_an_unusable_compliance_label_is_never_a_clean_result(real):
    compliance = ComplianceAgent(corpus=real["corpus"], classifier=_ArbitraryText())
    out = real_orchestrator(real, compliance=compliance).submit(QUESTION)

    result = out.session.results.compliance_inbound
    assert result.status == "error" and result.clean is False
    assert out.session.disposition == "HOLD_AGENT_FAILURE"
    assert out.session.draft is None


# ---------------------------------------------------------------------------
# The standing PII test
# ---------------------------------------------------------------------------


def _pii_samples():
    return json.loads((FIXTURES / "pii_samples.json").read_text(encoding="utf-8"))["samples"]


@pytest.mark.parametrize("sample", _pii_samples(), ids=lambda s: s["id"])
def test_no_raw_pii_reaches_state_logs_prompts_or_agents(real, sample):
    capture = Capture()
    seen: list[str] = []

    def spying(agent, method):
        def call(text, **kwargs):
            seen.append(repr((text, kwargs)))
            return getattr(agent, method)(text, **kwargs)

        return call

    agents = AgentSet(
        facts=spying(real["facts"], "run"),
        pattern=spying(real["pattern"], "run"),
        compliance=spying(real["compliance"], "inbound"),
    )
    orch = Orchestrator(agents, Drafter(writer=capture, corpus=real["corpus"]), logger=capture.logger)
    out = orch.submit(sample["text"])
    redo = orch.rerun(out.session, ["facts", "pattern"])

    everything = "\n".join(
        [
            repr(out.session),
            out.session.model_dump_json(),
            repr(out),
            repr(redo.session),
            redo.session.model_dump_json(),
            capture.everything_seen(),
            "\n".join(seen),
        ]
    )
    for raw in sample["raw_values"]:
        assert raw not in everything, (sample["id"], "raw value leaked")
    assert capture.logger.dropped_fields == []
