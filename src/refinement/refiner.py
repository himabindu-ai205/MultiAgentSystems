"""Carries out a refinement (Implementation Plan Phase 8, Architecture section 10).

The human pushes back on a draft. The router says which agents to re-run and with what
parameters. This class does the bookkeeping around that, and nothing else:

* **Cap.** At most five refinements per ticket. The sixth is refused with a clear message
  and runs nothing. Approve and discard stay available.
* **Redact first.** The feedback is redacted before the router or any model sees it. Only
  the redacted words are used, and they are never stored, logged or put in a result.
* **Re-dispatch.** The Orchestrator runs the Phase 6 graph with only the affected nodes and
  reuses the cached results for the rest. It then re-merges, re-drafts and has the Reviewer
  look again, the same as a first run.
* **Earlier choices stay.** Style, scheme, theme and window are kept in the session as fixed
  values, so a later refinement does not undo an earlier one.
* **Log.** One ``refinement`` event per accepted refinement: the round, the category and
  the agents fired and cached. Never the feedback.

A refused request is not a round. It runs nothing and logs nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from approval.gate import void_approval
from constants import MAX_REFINEMENT_ROUNDS
from ingress.redactor import IngressError, redact
from logging_allowlist import AllowListLogger
from orchestrator.dispatch import Orchestrator, RunOutput
from orchestrator.graph import NODE_NAMES, NodeParams
from refinement.router import RefinementPlan, RefinementRouter
from schemas import RefinementEntry, Session

DEFAULT_MAX_FEEDBACK_CHARS = 1000
# The desk's Refine button sends this. It is not a style request.
RERUN_ALL = "rerun"

REFUSAL_MESSAGES = {
    "REFINEMENT_CAP": (
        "The refinement limit of {cap} has been reached for this ticket. "
        "You can still approve or discard the current draft."
    ),
    "FEEDBACK_UNUSABLE": "Type what you want changed, in up to {chars} characters.",
    "TICKET_CLOSED": "This ticket has been closed, so it cannot be refined.",
}


@dataclass(frozen=True)
class Refinement:
    """The result of one ``refine`` call. It holds no feedback text."""

    status: Literal["done", "refused"]
    rounds_left: int
    run: RunOutput | None = None
    plan: RefinementPlan | None = None
    # A fixed code when refused.
    refusal: str | None = None
    # Fixed text to show the human: a refusal reason, or the router's notice.
    message: str | None = None


class Refiner:
    def __init__(
        self,
        orchestrator: Orchestrator,
        router: RefinementRouter,
        *,
        logger: AllowListLogger | None = None,
        max_rounds: int = MAX_REFINEMENT_ROUNDS,
        max_feedback_chars: int = DEFAULT_MAX_FEEDBACK_CHARS,
    ) -> None:
        if not 1 <= max_rounds <= MAX_REFINEMENT_ROUNDS:
            raise ValueError(f"max_rounds must be between 1 and {MAX_REFINEMENT_ROUNDS}")
        if max_feedback_chars < 1:
            raise ValueError("max_feedback_chars must be at least 1")
        self._orchestrator = orchestrator
        self._router = router
        self._logger = logger
        self._max_rounds = max_rounds
        self._max_chars = max_feedback_chars

    # -- public ------------------------------------------------------------

    def refine(self, session: Session, feedback: str) -> Refinement:
        """Apply one piece of feedback to the session's draft."""
        if session.approval.status == "rejected":
            return self._refuse(session, "TICKET_CLOSED")
        if session.refinement.rounds_used >= self._max_rounds:
            return self._refuse(session, "REFINEMENT_CAP")

        try:
            redacted = redact(feedback, max_chars=self._max_chars).redacted_text
        except IngressError:
            return self._refuse(session, "FEEDBACK_UNUSABLE")
        finally:
            feedback = None  # type: ignore[assignment]  # only the redacted words are used from here

        rerun_all = redacted.strip().lower() == RERUN_ALL
        if rerun_all:
            plan = RefinementPlan(category="unclear", nodes=NODE_NAMES, style="standard")
        else:
            plan = self._router.route(redacted)
        state = session.refinement
        style = "standard" if rerun_all else (plan.style or state.style)
        scheme = plan.scheme_override or state.scheme_override
        theme = plan.theme_hint or state.theme_hint
        window = state.window_shift_weeks if plan.window_shift_weeks is None else plan.window_shift_weeks

        run = self._orchestrator.rerun(
            session,
            plan.nodes,
            params=NodeParams(scheme_override=scheme, theme_hint=theme, window_shift_weeks=window),
            style=style,
            reword=rerun_all,
        )

        if session.approval.status == "approved":
            void_approval(session)

        # The run worked, so the round counts and the choices are kept.
        state.rounds_used += 1
        state.history = (
            *state.history,
            RefinementEntry(
                category=plan.category,
                agents_fired=run.agents_fired,
                agents_cached=run.agents_cached,
            ),
        )
        state.style, state.scheme_override, state.theme_hint = style, scheme, theme  # type: ignore[assignment]
        state.window_shift_weeks = window
        self._log(session, plan, run)

        return Refinement(
            status="done",
            rounds_left=self._max_rounds - state.rounds_used,
            run=run,
            plan=plan,
            message=plan.notice,
        )

    def discard(self, session: Session) -> None:
        """Drop the ticket. Nothing is saved, and it takes no more refinements."""
        session.approval.status = "rejected"

    def rounds_left(self, session: Session) -> int:
        return max(0, self._max_rounds - session.refinement.rounds_used)

    # -- internals ---------------------------------------------------------

    def _refuse(self, session: Session, code: str) -> Refinement:
        message = REFUSAL_MESSAGES[code].format(cap=self._max_rounds, chars=self._max_chars)
        return Refinement(
            status="refused",
            rounds_left=self.rounds_left(session),
            refusal=code,
            message=message,
        )

    def _log(self, session: Session, plan: RefinementPlan, run: RunOutput) -> None:
        if self._logger is None:
            return
        self._logger.log(
            "refinement",
            request_id=session.request_id,
            round=session.refinement.rounds_used,
            category=plan.category,
            agents_fired=run.agents_fired,
            agents_cached=run.agents_cached,
        )


__all__ = ["DEFAULT_MAX_FEEDBACK_CHARS", "Refinement", "Refiner", "REFUSAL_MESSAGES"]
