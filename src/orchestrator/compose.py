"""Merge, draft and route in one call (Implementation Plan Phase 5).

Takes the three agent results and returns the disposition, the reply draft and the
routing note. There is no concurrency and no redaction here. Phase 6 calls this after
it has collected the three results.
"""

from __future__ import annotations

from dataclasses import dataclass

from logging_allowlist import AllowListLogger
from orchestrator.disposition import AgentFailure, decide, failed_agents
from orchestrator.drafter import DraftOutcome, Drafter, Wording
from orchestrator.routing import build_routing_note
from schemas import (
    ComplianceResult,
    Disposition,
    FactsResult,
    PatternResult,
    PiiCounts,
    ReplyDraft,
    RoutingNote,
)


@dataclass(frozen=True)
class Outcome:
    disposition: Disposition
    draft: ReplyDraft | None
    routing_note: RoutingNote
    wording: Wording = "template"
    # Why the model wording was not used, or why drafting failed. A fixed code.
    draft_reason: str | None = None
    failures: tuple[AgentFailure, ...] = ()


def compose(
    request_id: str,
    facts: FactsResult | None,
    pattern: PatternResult | None,
    compliance: ComplianceResult | None,
    *,
    drafter: Drafter,
    pii_manifest: PiiCounts | None = None,
    advice_attempts: int = 0,
    style: str = "standard",
    rework: str | None = None,
    logger: AllowListLogger | None = None,
) -> Outcome:
    """Decide, draft and route. A drafting failure never reaches the human as a pass.

    If the drafting step cannot produce a safe draft for a disposition that needs one,
    the disposition becomes ``HOLD_AGENT_FAILURE`` and the note names the
    orchestrator, so an empty or unsafe reply is never presented as ready.
    """
    disposition = decide(facts, pattern, compliance)
    failures = failed_agents(facts, pattern, compliance)
    draft: ReplyDraft | None = None
    outcome = DraftOutcome(None)
    extra: tuple[AgentFailure, ...] = ()

    if disposition.value != "HOLD_AGENT_FAILURE":
        outcome = drafter.draft(disposition.value, facts, style=style, rework=rework)
        draft = outcome.draft
        if draft is None:
            disposition = Disposition(value="HOLD_AGENT_FAILURE")
            extra = (AgentFailure("orchestrator", outcome.reason_code or "DRAFT_FAILED"),)

    note = build_routing_note(
        disposition.value,
        facts,
        pattern,
        compliance,
        pii_manifest=pii_manifest,
        advice_attempts=advice_attempts,
        extra_failures=extra,
    )

    if logger is not None:
        logger.log(
            "disposition",
            request_id=request_id,
            value=disposition.value,
            signals=note.signals.model_dump(),
        )

    return Outcome(
        disposition=disposition,
        draft=draft,
        routing_note=note,
        wording=outcome.wording,
        draft_reason=outcome.reason_code,
        failures=failures + extra,
    )


__all__ = ["Outcome", "compose"]
