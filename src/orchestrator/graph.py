"""The LangGraph that starts the three specialists together (Architecture section 4).

The graph is fixed code. One entry step fans out to the chosen specialist nodes, and one
join step waits for them. There is no router node and no model choosing a path.

Rules this module keeps:

* **Isolation.** Every node runs in the same step, so none can see another's output. A
  node hands its agent only the redacted complaint and that agent's own parameters,
  never the graph state.
* **One slot each.** A node writes only its own result key and its own trace key.
* **Nothing raises into the graph.** A timeout, an exception or a result of the wrong
  type becomes ``status = "error"`` in that node's slot, so one failed branch never
  cancels the other two. Error codes are fixed strings. No exception text is kept,
  because it could contain the complaint.
* **Nothing is stored.** The graph is compiled with no checkpointer and is run with
  tracing switched off, so graph state is dropped when the run ends (C3).
"""

from __future__ import annotations

import contextlib
import contextvars
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from schemas import ComplianceResult, FactsResult, PatternResult

NODE_NAMES: tuple[str, ...] = ("facts", "pattern", "compliance_inbound")

DEFAULT_TIMEOUT_SECONDS = 30.0

# The agent name each node reports in its log events.
AGENT_OF_NODE = {"facts": "facts", "pattern": "pattern", "compliance_inbound": "compliance"}
_CODE_PREFIX = {"facts": "FACTS", "pattern": "PATTERN", "compliance_inbound": "COMPLIANCE"}

AgentResult = FactsResult | PatternResult | ComplianceResult


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeParams:
    """The parameters a refinement can pass. Each belongs to exactly one agent."""

    scheme_override: str | None = None  # Facts
    theme_hint: str | None = None  # Pattern
    window_shift_weeks: int = 0  # Pattern


@dataclass(frozen=True)
class AgentSet:
    """The three specialists, as plain callables.

    ``facts(text, *, scheme_override)``, ``pattern(text, *, theme_hint, window_shift_weeks)``
    and ``compliance(text)``. The real agents and the Phase 0 stubs both fit.
    """

    facts: Callable[..., FactsResult]
    pattern: Callable[..., PatternResult]
    compliance: Callable[..., ComplianceResult]

    @classmethod
    def from_agents(cls, facts: Any, pattern: Any, compliance: Any) -> AgentSet:
        """Wrap the real ``FactsAgent``, ``PatternAgent`` and ``ComplianceAgent``."""
        return cls(facts=facts.run, pattern=pattern.run, compliance=compliance.inbound)


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeTrace:
    """When one node ran and how it ended. Times are UTC. No complaint text."""

    node: str
    started_at: datetime
    ended_at: datetime
    status: str  # the result's status, or "timeout"
    error_code: str | None = None

    @property
    def agent(self) -> str:
        return AGENT_OF_NODE[self.node]


@dataclass(frozen=True)
class GraphRun:
    """What one graph run returns: a result and a trace for each node that ran."""

    results: Mapping[str, AgentResult]
    traces: Mapping[str, NodeTrace]


def start_spread_ms(traces: Mapping[str, NodeTrace]) -> float:
    """Milliseconds between the first and the last node start."""
    starts = [t.started_at for t in traces.values()]
    return (max(starts) - min(starts)).total_seconds() * 1000 if starts else 0.0


def ran_together(traces: Mapping[str, NodeTrace]) -> bool:
    """True when every node had started before any node had ended."""
    if not traces:
        return False
    return max(t.started_at for t in traces.values()) < min(t.ended_at for t in traces.values())


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------


class GraphState(TypedDict, total=False):
    # Inputs. Only the redacted complaint and the parameters. Never the raw text.
    redacted_text: str
    run_nodes: tuple[str, ...]
    scheme_override: str | None
    theme_hint: str | None
    window_shift_weeks: int
    # One result slot and one trace slot per node.
    facts: FactsResult
    pattern: PatternResult
    compliance_inbound: ComplianceResult
    facts_trace: NodeTrace
    pattern_trace: NodeTrace
    compliance_inbound_trace: NodeTrace
    joined: bool


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _error_result(node: str, code: str) -> AgentResult:
    if node == "facts":
        return FactsResult(status="error", error=code)
    if node == "pattern":
        return PatternResult(status="error", error=code)
    return ComplianceResult(mode="inbound", status="error", clean=False, error=code)


def _is_valid(node: str, result: object) -> bool:
    if node == "facts":
        return isinstance(result, FactsResult)
    if node == "pattern":
        return isinstance(result, PatternResult)
    return isinstance(result, ComplianceResult) and result.mode == "inbound"


_TIMED_OUT = object()


class _Raised:
    """Marks that the agent raised. Holds nothing from the exception."""


def _call_with_timeout(fn: Callable[[], Any], timeout: float) -> Any:
    """Run ``fn`` on its own thread and wait at most ``timeout`` seconds.

    The thread is a daemon, so an agent that never returns cannot keep the process
    alive. The caller's context is copied, so the tracing switch carries over.
    """
    slot: list[Any] = []
    context = contextvars.copy_context()

    def target() -> None:
        try:
            slot.append(context.run(fn))
        except BaseException:  # noqa: BLE001 - nothing from the exception is kept
            slot.append(_Raised())

    thread = threading.Thread(target=target, name="agent-call", daemon=True)
    thread.start()
    thread.join(timeout)
    return slot[0] if slot else _TIMED_OUT


def _make_node(
    node: str,
    call: Callable[[GraphState], Any],
    timeout_seconds: float,
) -> Callable[[GraphState], dict[str, Any]]:
    prefix = _CODE_PREFIX[node]

    def run(state: GraphState) -> dict[str, Any]:
        started = _now()
        outcome = _call_with_timeout(lambda: call(state), timeout_seconds)
        ended = _now()

        status: str
        code: str | None = None
        if outcome is _TIMED_OUT:
            status, code = "timeout", f"{prefix}_TIMEOUT"
            result = _error_result(node, code)
        elif isinstance(outcome, _Raised):
            status, code = "error", f"{prefix}_EXCEPTION"
            result = _error_result(node, code)
        elif not _is_valid(node, outcome):
            status, code = "error", f"{prefix}_INVALID_RESULT"
            result = _error_result(node, code)
        else:
            result = outcome
            status = result.status
            code = result.error if result.status == "error" else None

        trace = NodeTrace(node=node, started_at=started, ended_at=ended, status=status, error_code=code)
        return {node: result, f"{node}_trace": trace}

    run.__name__ = node
    return run


def _select_nodes(state: GraphState) -> list[str]:
    return list(state["run_nodes"])


def _join(state: GraphState) -> dict[str, Any]:
    # The join only waits. It reads nothing and merges nothing. The Orchestrator
    # merges after the graph returns.
    return {"joined": True}


def build_graph(agents: AgentSet, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS):
    """Compile the fixed fan-out and join. No checkpointer, so nothing is saved."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    calls: dict[str, Callable[[GraphState], Any]] = {
        "facts": lambda s: agents.facts(s["redacted_text"], scheme_override=s.get("scheme_override")),
        "pattern": lambda s: agents.pattern(
            s["redacted_text"],
            theme_hint=s.get("theme_hint"),
            window_shift_weeks=s.get("window_shift_weeks", 0),
        ),
        "compliance_inbound": lambda s: agents.compliance(s["redacted_text"]),
    }

    graph = StateGraph(GraphState)
    for node in NODE_NAMES:
        graph.add_node(node, _make_node(node, calls[node], timeout_seconds))
    graph.add_node("join", _join)

    # One fan-out. The chosen nodes start in the same step.
    graph.add_conditional_edges(START, _select_nodes, list(NODE_NAMES))
    for node in NODE_NAMES:
        graph.add_edge(node, "join")
    graph.add_edge("join", END)
    return graph.compile(checkpointer=None)


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _tracing_off() -> Iterator[None]:
    """Switch external tracing off, whatever the environment says."""
    try:
        from langsmith.run_helpers import tracing_context
    except ImportError:  # nothing to switch off
        yield
        return
    with tracing_context(enabled=False):
        yield


def _check_nodes(nodes: Sequence[str]) -> tuple[str, ...]:
    chosen = tuple(dict.fromkeys(nodes))
    if not chosen:
        raise ValueError("at least one node must be chosen")
    unknown = [n for n in chosen if n not in NODE_NAMES]
    if unknown:
        raise ValueError("unknown node: " + ", ".join(unknown))
    return chosen


def run_graph(
    graph: Any,
    redacted_text: str,
    nodes: Sequence[str],
    params: NodeParams = NodeParams(),
) -> GraphRun:
    """Run the chosen nodes together and return their results and traces.

    Raises ``ValueError`` before anything starts if the selection or the text is unusable.
    """
    chosen = _check_nodes(nodes)
    if not isinstance(redacted_text, str) or not redacted_text.strip():
        raise ValueError("redacted_text must be non-empty text")

    state: GraphState = {
        "redacted_text": redacted_text,
        "run_nodes": chosen,
        "scheme_override": params.scheme_override,
        "theme_hint": params.theme_hint,
        "window_shift_weeks": params.window_shift_weeks,
    }
    with _tracing_off():
        final = graph.invoke(state)

    results = {n: final[n] for n in chosen}
    traces = {n: final[f"{n}_trace"] for n in chosen}
    return GraphRun(results=MappingProxyType(results), traces=MappingProxyType(traces))


__all__ = [
    "AGENT_OF_NODE",
    "AgentResult",
    "AgentSet",
    "DEFAULT_TIMEOUT_SECONDS",
    "GraphRun",
    "NODE_NAMES",
    "NodeParams",
    "NodeTrace",
    "build_graph",
    "ran_together",
    "run_graph",
    "start_spread_ms",
]
