"""Phase 6: the LangGraph that starts the three specialists together."""

from __future__ import annotations

import threading
import time

import pytest

from agents.stubs import stub_compliance, stub_facts, stub_pattern
from orchestrator.graph import (
    NODE_NAMES,
    AgentSet,
    NodeParams,
    build_graph,
    ran_together,
    run_graph,
    start_spread_ms,
)
from schemas import ComplianceResult, FactsResult, PatternResult

TEXT = "my fund question, no personal data"


class Recorder:
    """Fake agents that remember exactly what each one was called with."""

    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls: dict[str, list[tuple[tuple, dict]]] = {n: [] for n in ("facts", "pattern", "compliance")}
        self.lock = threading.Lock()

    def _note(self, name: str, args: tuple, kwargs: dict) -> None:
        with self.lock:
            self.calls[name].append((args, kwargs))
        time.sleep(self.delay)

    def agents(self) -> AgentSet:
        def facts(*args, **kwargs) -> FactsResult:
            self._note("facts", args, kwargs)
            return stub_facts()

        def pattern(*args, **kwargs) -> PatternResult:
            self._note("pattern", args, kwargs)
            return stub_pattern(scenario="recurring")

        def compliance(*args, **kwargs) -> ComplianceResult:
            self._note("compliance", args, kwargs)
            return stub_compliance()

        return AgentSet(facts=facts, pattern=pattern, compliance=compliance)


def graph_for(agents: AgentSet, timeout: float = 5.0):
    return build_graph(agents, timeout_seconds=timeout)


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_graph_has_exactly_three_specialists_one_fan_out_and_one_join():
    drawn = graph_for(Recorder().agents()).get_graph()

    assert set(drawn.nodes) == {"__start__", "facts", "pattern", "compliance_inbound", "join", "__end__"}
    edges = {(e.source, e.target) for e in drawn.edges}
    assert edges == {
        ("__start__", "facts"),
        ("__start__", "pattern"),
        ("__start__", "compliance_inbound"),
        ("facts", "join"),
        ("pattern", "join"),
        ("compliance_inbound", "join"),
        ("join", "__end__"),
    }
    assert NODE_NAMES == ("facts", "pattern", "compliance_inbound")


def test_graph_has_no_checkpointer_so_nothing_is_stored():
    assert graph_for(Recorder().agents()).checkpointer is None


def test_tracing_is_off_inside_an_agent_even_if_the_environment_turns_it_on(monkeypatch):
    from langsmith.utils import tracing_is_enabled

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    seen: list[object] = []

    def facts(text, **kwargs):
        seen.append(tracing_is_enabled())
        return stub_facts()

    agents = AgentSet(facts=facts, pattern=lambda t, **k: stub_pattern(), compliance=lambda t: stub_compliance())
    run_graph(graph_for(agents), TEXT, NODE_NAMES, NodeParams())

    assert seen == [False]


# ---------------------------------------------------------------------------
# Parallel start
# ---------------------------------------------------------------------------


def test_the_three_agents_start_together_and_overlap():
    recorder = Recorder(delay=0.3)
    started = time.perf_counter()
    run = run_graph(graph_for(recorder.agents()), TEXT, NODE_NAMES, NodeParams())
    elapsed = time.perf_counter() - started

    assert set(run.results) == set(NODE_NAMES)
    assert ran_together(run.traces)
    assert start_spread_ms(run.traces) < 150
    # Run one after another this would take 0.9 seconds or more.
    assert elapsed < 0.75


def test_a_slow_agent_does_not_hold_up_the_other_two_and_a_timeout_cancels_nothing():
    release = threading.Event()

    def slow_facts(text, **kwargs):
        release.wait(timeout=2)
        return stub_facts()

    agents = AgentSet(
        facts=slow_facts,
        pattern=lambda t, **k: stub_pattern(scenario="rising"),
        compliance=lambda t: stub_compliance(),
    )
    try:
        run = run_graph(graph_for(agents, timeout=0.2), TEXT, NODE_NAMES, NodeParams())
    finally:
        release.set()

    facts = run.results["facts"]
    assert facts.status == "error" and facts.error == "FACTS_TIMEOUT"
    assert run.results["pattern"].signal == "rising"
    assert run.results["compliance_inbound"].clean is True
    assert run.traces["facts"].status == "timeout"
    assert run.traces["pattern"].ended_at < run.traces["facts"].ended_at
    assert run.traces["compliance_inbound"].ended_at < run.traces["facts"].ended_at


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


def test_each_agent_gets_only_the_redacted_text_and_its_own_parameters():
    recorder = Recorder()
    params = NodeParams(scheme_override="mid_cap", theme_hint="fees_brokerage", window_shift_weeks=1)
    run_graph(graph_for(recorder.agents()), TEXT, NODE_NAMES, params)

    assert recorder.calls["facts"] == [((TEXT,), {"scheme_override": "mid_cap"})]
    assert recorder.calls["pattern"] == [
        ((TEXT,), {"theme_hint": "fees_brokerage", "window_shift_weeks": 1})
    ]
    assert recorder.calls["compliance"] == [((TEXT,), {})]


def test_each_node_writes_only_its_own_keys():
    graph = graph_for(Recorder().agents())
    allowed = {
        "facts": {"facts", "facts_trace"},
        "pattern": {"pattern", "pattern_trace"},
        "compliance_inbound": {"compliance_inbound", "compliance_inbound_trace"},
        "join": {"joined"},
    }
    seen: set[str] = set()
    state = {"redacted_text": TEXT, "run_nodes": NODE_NAMES}
    for chunk in graph.stream(state, stream_mode="updates"):
        for node, update in chunk.items():
            seen.add(node)
            assert set(update or {}) <= allowed[node], node
    assert seen == set(allowed)


# ---------------------------------------------------------------------------
# Failures become error results, never exceptions
# ---------------------------------------------------------------------------


def test_an_exception_becomes_an_error_result_without_its_message():
    def facts(text, **kwargs):
        raise RuntimeError("secret value 9876543210")

    agents = AgentSet(
        facts=facts,
        pattern=lambda t, **k: stub_pattern(),
        compliance=lambda t: stub_compliance(),
    )
    run = run_graph(graph_for(agents), TEXT, NODE_NAMES, NodeParams())

    assert run.results["facts"].status == "error"
    assert run.results["facts"].error == "FACTS_EXCEPTION"
    assert "9876543210" not in repr(run)
    assert run.results["pattern"].status == "ok"
    assert run.results["compliance_inbound"].status == "ok"
    assert run.traces["facts"].status == "error"
    assert run.traces["facts"].error_code == "FACTS_EXCEPTION"


@pytest.mark.parametrize(
    ("node", "make", "expected"),
    [
        ("facts", lambda: AgentSet(lambda t, **k: "nope", lambda t, **k: stub_pattern(), lambda t: stub_compliance()), "FACTS_INVALID_RESULT"),
        ("pattern", lambda: AgentSet(lambda t, **k: stub_facts(), lambda t, **k: None, lambda t: stub_compliance()), "PATTERN_INVALID_RESULT"),
        ("compliance_inbound", lambda: AgentSet(lambda t, **k: stub_facts(), lambda t, **k: stub_pattern(), lambda t: stub_facts()), "COMPLIANCE_INVALID_RESULT"),
    ],
)
def test_a_result_of_the_wrong_type_becomes_an_error_result(node, make, expected):
    run = run_graph(graph_for(make()), TEXT, NODE_NAMES, NodeParams())
    assert run.results[node].status == "error"
    assert run.results[node].error == expected


def test_an_error_result_from_a_compliance_failure_is_never_clean():
    def compliance(text):
        raise ValueError("boom")

    agents = AgentSet(lambda t, **k: stub_facts(), lambda t, **k: stub_pattern(), compliance)
    run = run_graph(graph_for(agents), TEXT, NODE_NAMES, NodeParams())
    result = run.results["compliance_inbound"]
    assert result.status == "error" and result.clean is False and result.mode == "inbound"


# ---------------------------------------------------------------------------
# Choosing which nodes run
# ---------------------------------------------------------------------------


def test_a_subset_starts_only_those_nodes():
    recorder = Recorder()
    run = run_graph(graph_for(recorder.agents()), TEXT, ("pattern",), NodeParams())

    assert set(run.results) == {"pattern"}
    assert set(run.traces) == {"pattern"}
    assert len(recorder.calls["pattern"]) == 1
    assert recorder.calls["facts"] == [] and recorder.calls["compliance"] == []


def test_two_chosen_nodes_still_start_together():
    recorder = Recorder(delay=0.3)
    run = run_graph(graph_for(recorder.agents()), TEXT, ("facts", "pattern"), NodeParams())

    assert set(run.results) == {"facts", "pattern"}
    assert ran_together(run.traces)


@pytest.mark.parametrize("nodes", [(), ("nope",), ("facts", "nope")])
def test_an_empty_or_unknown_selection_is_rejected_before_anything_runs(nodes):
    recorder = Recorder()
    with pytest.raises(ValueError):
        run_graph(graph_for(recorder.agents()), TEXT, nodes, NodeParams())
    assert all(not calls for calls in recorder.calls.values())


def test_a_blank_text_is_rejected_before_anything_runs():
    recorder = Recorder()
    with pytest.raises(ValueError):
        run_graph(graph_for(recorder.agents()), "   ", NODE_NAMES, NodeParams())
    assert all(not calls for calls in recorder.calls.values())
