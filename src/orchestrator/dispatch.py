"""The Orchestrator's dispatch and gate (Implementation Plan Phase 6, Architecture section 8).

One owner of control flow, all of it deterministic code:

1. Redact the raw complaint with Ingress. If that fails, stop. No agent starts.
2. Run the Phase 6 graph, so the chosen specialists start together.
3. Collect the results and pass them to the Phase 5 merge and drafting logic.
4. Hand the draft, the routing note and the evidence to the Reviewer.

The raw complaint exists only inside ``submit``, as one local variable that is cleared
as soon as redaction returns. It is never put in the session, the graph, a log or a
prompt. A refinement (``rerun``) starts from the redacted complaint in the session.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from agents.compliance import is_advice_request
from constants import FiredAgent
from ingress.redactor import DEFAULT_MAX_CHARS, redact
from logging_allowlist import AllowListLogger
from orchestrator.compose import Outcome, compose
from orchestrator.drafter import Drafter
from orchestrator.graph import (
    AGENT_OF_NODE,
    DEFAULT_TIMEOUT_SECONDS,
    NODE_NAMES,
    AgentSet,
    NodeParams,
    NodeTrace,
    build_graph,
    run_graph,
)
from schemas import (
    PiiCounts,
    ReviewerResult,
    ReviewerState,
    ReviewHandoff,
    Session,
)

# Anything that takes a handoff and returns a verdict: the real ``Reviewer``, or a stand-in.
Reviewer = Callable[[ReviewHandoff], ReviewerResult]


@dataclass(frozen=True)
class RunOutput:
    """The result of one run. ``session`` is the same object that was passed in or built."""

    session: Session
    outcome: Outcome
    # One trace per node that ran in this run. A reused result has no trace.
    traces: Mapping[str, NodeTrace]
    # For the refinement log: who ran this time, and whose cached result was reused.
    agents_fired: tuple[FiredAgent, ...]
    agents_cached: tuple[FiredAgent, ...]
    review: ReviewerResult | None = None


class Orchestrator:
    """Redact, dispatch, merge, draft, and hand the draft on."""

    def __init__(
        self,
        agents: AgentSet,
        drafter: Drafter,
        *,
        logger: AllowListLogger | None = None,
        reviewer: Reviewer | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_complaint_chars: int = DEFAULT_MAX_CHARS,
    ) -> None:
        self._graph = build_graph(agents, timeout_seconds=timeout_seconds)
        self._drafter = drafter
        self._logger = logger
        self._reviewer = reviewer
        self._max_chars = max_complaint_chars

    # -- public ------------------------------------------------------------

    def submit(self, raw_complaint: str, *, request_id: str | None = None) -> RunOutput:
        """A first run. Raises ``IngressError`` if redaction fails, before any agent starts."""
        try:
            redaction = redact(raw_complaint, request_id=request_id, max_chars=self._max_chars)
        finally:
            # The only other reference is the caller's. Nothing here keeps the raw text.
            raw_complaint = None  # type: ignore[assignment]

        session = Session(
            request_id=redaction.request_id,
            redacted_text=redaction.redacted_text,
            pii_manifest=PiiCounts(**dict(redaction.manifest)),
        )
        return self._run(session, NODE_NAMES, NodeParams(), style="standard", first_run=True)

    def rerun(
        self,
        session: Session,
        nodes: Sequence[str],
        *,
        params: NodeParams = NodeParams(),
        style: str = "standard",
        reword: bool = False,
    ) -> RunOutput:
        """Run only ``nodes``, reuse the cached results for the rest, then merge and redraft.

        ``nodes`` may be empty, for a change that only needs a new draft. The redaction step
        is skipped: the session already holds the redacted complaint.
        """
        chosen = tuple(dict.fromkeys(nodes))
        unknown = [n for n in chosen if n not in NODE_NAMES]
        if unknown:
            raise ValueError("unknown agent: " + ", ".join(unknown))
        for node in NODE_NAMES:
            if node not in chosen and getattr(session.results, node) is None:
                raise ValueError(f"no cached result for {node}, so it has to run")
        return self._run(session, chosen, params, style=style, reword=reword)

    # -- internals ---------------------------------------------------------

    def _run(
        self,
        session: Session,
        nodes: tuple[str, ...],
        params: NodeParams,
        *,
        style: str,
        first_run: bool = False,
        reword: bool = False,
    ) -> RunOutput:
        traces: Mapping[str, NodeTrace] = MappingProxyType({})
        if nodes:
            run = run_graph(self._graph, session.redacted_text, nodes, params)
            traces = run.traces
            # The cached results are put back here, after the run. They are never passed to a node.
            for node, result in run.results.items():
                setattr(session.results, node, result)
            self._log_agent_runs(session.request_id, traces)

        # An advice request is counted once, on the first run. A rerun of the same text is
        # not a new request.
        compliance = session.results.compliance_inbound
        if first_run and compliance is not None and is_advice_request(compliance):
            session.advice_attempts += 1

        return self._finish(session, traces, nodes, style=style, reword=reword)

    def _compose(self, session: Session, style: str, *, rework: str | None = None, reword: bool = False, log: bool = True) -> Outcome:
        results = session.results
        return compose(
            session.request_id,
            results.facts,
            results.pattern,
            results.compliance_inbound,
            drafter=self._drafter,
            pii_manifest=session.pii_manifest,
            advice_attempts=session.advice_attempts,
            style=style,
            rework=rework,
            reword=reword,
            logger=self._logger if log else None,
        )

    def _handoff(self, session: Session, outcome: Outcome, round: int) -> ReviewHandoff:
        assert outcome.draft is not None
        return ReviewHandoff(
            request_id=session.request_id,
            draft=outcome.draft,
            routing_note=outcome.routing_note,
            facts=session.results.facts,
            round=round,
        )

    def _review(
        self, session: Session, outcome: Outcome, style: str
    ) -> tuple[Outcome, ReviewerResult, ReviewerState]:
        """Review a draft, allow it one rework, and settle the result (C6).

        Round 1 looks at the draft. On a hard fail the Orchestrator drafts once more, with
        the failure named, and the Reviewer looks again. A second failure is final: the
        draft is flagged. The draft is never drafted a third time.
        """
        assert self._reviewer is not None
        review = self._reviewer(self._handoff(session, outcome, round=1))
        if review.verdict == "PASS" or review.flagged:
            return outcome, review, ReviewerState(
                rounds_used=1, last_verdict=review.verdict, flagged=review.flagged
            )

        # The one rework. Agents are not rerun and the disposition does not change, so the
        # merge result is the same. The disposition is not logged a second time.
        redo = self._compose(session, style, rework=review.instruction, log=False)
        if redo.draft is None:
            # No draft to review or to show. The human sees a failure, never a pass.
            return redo, review, ReviewerState(rounds_used=1, last_verdict="FAIL", flagged=False)

        review = self._reviewer(self._handoff(session, redo, round=2))
        if review.verdict == "FAIL" and not review.flagged:
            # The approve control depends on this flag, so it is not left to the Reviewer.
            review = ReviewerResult(
                verdict="FAIL",
                round=review.round,
                failure_types=review.failure_types,
                instruction=review.instruction,
                flagged=True,
            )
        return redo, review, ReviewerState(
            rounds_used=2, last_verdict=review.verdict, flagged=review.flagged
        )

    def _finish(self, session: Session, traces: Mapping[str, NodeTrace], nodes: tuple[str, ...], *, style: str, reword: bool = False) -> RunOutput:
        outcome = self._compose(session, style, reword=reword)

        review: ReviewerResult | None = None
        state = ReviewerState()
        if self._reviewer is not None and outcome.draft is not None:
            outcome, review, state = self._review(session, outcome, style)

        session.draft = outcome.draft
        session.routing_note = outcome.routing_note
        session.disposition = outcome.disposition.value
        session.reviewer = state

        fired = tuple(AGENT_OF_NODE[n] for n in NODE_NAMES if n in nodes)
        cached = tuple(AGENT_OF_NODE[n] for n in NODE_NAMES if n not in nodes)
        return RunOutput(
            session=session,
            outcome=outcome,
            traces=traces,
            agents_fired=(*fired, "orchestrator"),  # type: ignore[arg-type]
            agents_cached=cached,  # type: ignore[arg-type]
            review=review,
        )

    def _log_agent_runs(self, request_id: str, traces: Mapping[str, NodeTrace]) -> None:
        if self._logger is None:
            return
        for node in NODE_NAMES:
            trace = traces.get(node)
            if trace is None:
                continue
            fields: dict[str, object] = {
                "request_id": request_id,
                "agent": trace.agent,
                "status": trace.status,
                "duration_ms": (trace.ended_at - trace.started_at).total_seconds() * 1000,
                "started_at": trace.started_at.isoformat(),
                "ended_at": trace.ended_at.isoformat(),
            }
            if trace.error_code:
                fields["error_code"] = trace.error_code
            self._logger.log("agent_run", **fields)


__all__ = ["Orchestrator", "ReviewHandoff", "Reviewer", "RunOutput"]
