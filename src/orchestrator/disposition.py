"""The decision table (Architecture section 8.1) as a pure function.

Three results go in and one ``Disposition`` comes out. There is no model call, no
clock and no state, so the same results always give the same disposition.

Rows, in the order they are applied:

1. Any agent that failed (status ``error``, or no result at all) gives
   ``HOLD_AGENT_FAILURE``. This comes first, because a result from a failed run cannot
   be trusted and the table says an agent error always ends in no draft.
2. Compliance found an advice or returns request gives ``REFUSAL``, whatever Facts and
   Pattern say. Pattern then appears in the routing note only.
3. Facts ``covered`` and Pattern ``rising`` gives ``HOLD_DISAGREEMENT``.
4. Facts ``covered`` and Pattern ``one_off`` or ``recurring`` gives ``SEND_READY``.
5. Facts ``not_covered`` or ``ambiguous`` gives ``NOT_COVERED``.

A ``pii_residual`` flag on the inbound text does not change the outcome. Ingress has
already redacted the text, so the flag is only recorded in the routing note.
Any other flag type on an inbound result is treated as an advice request, so an
unexpected flag moves the ticket towards a refusal and never towards a send.
"""

from __future__ import annotations

from dataclasses import dataclass

from schemas import ComplianceResult, Disposition, FactsResult, PatternResult


@dataclass(frozen=True)
class AgentFailure:
    """An agent that did not give a usable result. Carries a code, never text."""

    agent: str
    code: str


def failed_agents(
    facts: FactsResult | None,
    pattern: PatternResult | None,
    compliance: ComplianceResult | None,
) -> tuple[AgentFailure, ...]:
    failures: list[AgentFailure] = []
    if facts is None:
        failures.append(AgentFailure("facts", "AGENT_MISSING"))
    elif facts.status == "error":
        failures.append(AgentFailure("facts", facts.error or "AGENT_ERROR"))
    if pattern is None:
        failures.append(AgentFailure("pattern", "AGENT_MISSING"))
    elif pattern.status == "error":
        failures.append(AgentFailure("pattern", pattern.error or "AGENT_ERROR"))
    if compliance is None:
        failures.append(AgentFailure("compliance", "AGENT_MISSING"))
    elif compliance.status == "error":
        failures.append(AgentFailure("compliance", compliance.error or "AGENT_ERROR"))
    elif compliance.mode != "inbound":
        failures.append(AgentFailure("compliance", "COMPLIANCE_WRONG_MODE"))
    return tuple(failures)


def is_advice_request(compliance: ComplianceResult) -> bool:
    """True when the inbound scan found anything other than residual PII."""
    return compliance.status == "ok" and any(f.type != "pii_residual" for f in compliance.flags)


def decide(
    facts: FactsResult | None,
    pattern: PatternResult | None,
    compliance: ComplianceResult | None,
) -> Disposition:
    if failed_agents(facts, pattern, compliance):
        return Disposition(value="HOLD_AGENT_FAILURE")

    assert facts is not None and pattern is not None and compliance is not None

    if is_advice_request(compliance):
        return Disposition(value="REFUSAL")

    if facts.status == "covered":
        if pattern.signal == "rising":
            return Disposition(value="HOLD_DISAGREEMENT")
        return Disposition(value="SEND_READY")

    # not_covered or ambiguous. An "error" status was handled in the first row.
    return Disposition(value="NOT_COVERED")


__all__ = ["AgentFailure", "decide", "failed_agents", "is_advice_request"]
