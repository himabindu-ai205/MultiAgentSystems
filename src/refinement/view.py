"""What the human sees about a ticket (Implementation Plan Phase 8).

``build_view`` collects the facts the person needs from the session. ``render`` turns them
into plain text for the command-line interface. Nothing here decides anything about the
reply: the controls follow from the session's state, in one place, so a flagged or
unreviewed draft can never be shown with an approve control.

The view holds only what the session holds: the redacted complaint, the draft and its
source, the routing note, codes and counters. There is no raw complaint to show.
"""

from __future__ import annotations

from dataclasses import dataclass

from approval.gate import mcp_complete
from constants import MAX_REFINEMENT_ROUNDS
from orchestrator.dispatch import RunOutput
from schemas import Session

ALL_AGENTS = ("facts", "pattern", "compliance", "orchestrator")

REFINE, APPROVE, DISCARD, RETRY = "refine", "approve", "discard", "retry"

NOT_REVIEWED = "NOT REVIEWED"


@dataclass(frozen=True)
class HumanView:
    request_id: str
    complaint: str
    draft_text: str | None
    citation: str | None
    last_checked: str | None
    team: str | None
    priority: str | None
    routing_reason: str | None
    disposition: str | None
    disagreement: str | None
    verdict: str
    flagged: bool
    failure_types: tuple[str, ...]
    agents_fired: tuple[str, ...]
    refinements_left: int
    closed: bool
    # Which of "refine", "approve" and "discard" are on offer, in that order.
    controls: tuple[str, ...]
    notice: str | None = None


def _agents_fired(session: Session, run: RunOutput | None) -> tuple[str, ...]:
    if run is not None:
        return tuple(run.agents_fired)
    history = session.refinement.history
    return tuple(history[-1].agents_fired) if history else ALL_AGENTS


def _verdict(session: Session) -> str:
    verdict = session.reviewer.last_verdict
    return verdict if verdict is not None else NOT_REVIEWED


def build_view(
    session: Session,
    *,
    run: RunOutput | None = None,
    notice: str | None = None,
    max_rounds: int = MAX_REFINEMENT_ROUNDS,
) -> HumanView:
    """The view of a ticket now. ``run`` adds the Reviewer's reasons for the last draft."""
    draft, note = session.draft, session.routing_note
    left = max(0, max_rounds - session.refinement.rounds_used)
    written = session.approval.status == "approved" and mcp_complete(session)
    closed = session.approval.status == "rejected" or written
    verdict = _verdict(session)
    flagged = session.reviewer.flagged
    failures = tuple(run.review.failure_types) if run is not None and run.review is not None else ()

    controls: list[str] = []
    if not closed:
        if left > 0:
            controls.append(REFINE)
        if session.approval.status == "approved" and not written:
            controls.append(RETRY)
        elif draft is not None and verdict == "PASS" and not flagged:
            controls.append(APPROVE)
        controls.append(DISCARD)

    return HumanView(
        request_id=session.request_id,
        complaint=session.redacted_text,
        draft_text=draft.text if draft else None,
        citation=draft.citation if draft else None,
        last_checked=draft.last_checked if draft else None,
        team=note.team if note else None,
        priority=note.priority if note else None,
        routing_reason=note.reason if note else None,
        disposition=session.disposition,
        disagreement=note.disagreement if note else None,
        verdict=verdict,
        flagged=flagged,
        failure_types=failures,
        agents_fired=_agents_fired(session, run),
        refinements_left=left,
        closed=closed,
        controls=tuple(controls),
        notice=notice,
    )


def render(view: HumanView) -> str:
    """Plain text for the command line."""
    lines = [
        f"Ticket {view.request_id}",
        "=" * 60,
        "Complaint (personal details removed):",
        f"  {view.complaint}",
        "",
        "Reply draft:",
    ]
    if view.draft_text is None:
        lines.append("  (no draft: an agent failed, so a person needs to look at this ticket)")
    else:
        lines.append(f"  {view.draft_text}")
        lines.append(f"  Source: {view.citation or '(none)'}")
        lines.append(f"  Last checked: {view.last_checked or '(not shown)'}")

    lines += ["", f"Routing: {view.team or '(none)'}  Priority: {view.priority or '(none)'}"]
    if view.routing_reason:
        lines.append(f"  Why: {view.routing_reason}")
    if view.disagreement:
        lines.append(f"Agents disagree: {view.disagreement}")

    lines.append(f"Reviewer: {view.verdict}")
    if view.failure_types:
        lines.append("  Reasons: " + ", ".join(view.failure_types))
    if view.flagged:
        lines.append("  This draft is flagged: it failed review again after the rework, so it cannot be sent as it is.")
    lines.append("Agents that ran: " + ", ".join(view.agents_fired))
    if view.notice:
        lines += ["", f"Note: {view.notice}"]

    lines.append("")
    if view.closed:
        lines.append("This ticket is closed.")
    else:
        if REFINE in view.controls:
            lines.append(f"  [r] Refine ({view.refinements_left} left): type r, then what to change")
        elif view.refinements_left == 0:
            lines.append("  Refinement limit reached.")
        if RETRY in view.controls:
            lines.append("  [a] Retry (approval is kept; the write can be tried again)")
        if APPROVE in view.controls:
            lines.append("  [a] Approve")
        lines.append("  [d] Discard")
    return "\n".join(lines)


__all__ = ["APPROVE", "DISCARD", "HumanView", "NOT_REVIEWED", "REFINE", "RETRY", "build_view", "render"]
