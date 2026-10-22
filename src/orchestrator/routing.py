"""The routing note (Architecture section 8.3).

Team and priority come from the data tables below. They are design choices, so they
are easy to find and change. The note is built in code from the three results. It
holds counts, dates, codes and theme names only, never any complaint text.
"""

from __future__ import annotations

from schemas import (
    ComplianceResult,
    DispositionValue,
    FactsResult,
    PatternResult,
    PiiCounts,
    RoutingNote,
    Signals,
)
from orchestrator.disposition import AgentFailure, failed_agents, is_advice_request

# ---------------------------------------------------------------------------
# Data tables
# ---------------------------------------------------------------------------

FUNDS_TEAM = "Mutual Funds Support"
GENERAL_TEAM = "General Support"
COMPLIANCE_TEAM = "Compliance review"

THEME_TEAMS: dict[str, str] = {
    "trading_charts": "Trading & Orders Support",
    "money_movement": "Payments & Withdrawals Ops",
    "reliability": "App Technical Support",
    "fees_brokerage": "Fees & Billing Support",
    "other": GENERAL_TEAM,
}

SIGNAL_PRIORITY: dict[str, str] = {"rising": "P1", "recurring": "P2", "one_off": "P3"}
RAISED: dict[str, str] = {"P3": "P2", "P2": "P1", "P1": "P1"}
RAISED_THEMES: frozenset[str] = frozenset({"money_movement"})

# A run with a failed agent has no draft, so a person has to pick it up.
FAILURE_PRIORITY = "P2"
DEFAULT_PRIORITY = "P3"


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------


def compliance_signal(compliance: ComplianceResult | None) -> str:
    if compliance is None or compliance.status == "error":
        return "error"
    if is_advice_request(compliance):
        return "advice_request"
    if compliance.flags:
        return "pii_redacted"
    return "clean"


def build_signals(
    facts: FactsResult | None,
    pattern: PatternResult | None,
    compliance: ComplianceResult | None,
) -> Signals:
    if facts is None:
        facts_signal = "error"
    else:
        facts_signal = facts.status
    if pattern is None or pattern.status == "error" or pattern.signal is None:
        pattern_signal = "error"
    else:
        pattern_signal = pattern.signal
    return Signals(facts=facts_signal, pattern=pattern_signal, compliance=compliance_signal(compliance))


def teams_for(
    disposition: DispositionValue,
    facts: FactsResult | None,
    pattern: PatternResult | None,
    compliance: ComplianceResult | None,
) -> list[str]:
    """Owning team first, then any extra team that should see the note."""
    if disposition == "HOLD_AGENT_FAILURE":
        return [GENERAL_TEAM]

    theme_team = GENERAL_TEAM
    signal = None
    if pattern is not None and pattern.status == "ok":
        theme_team = THEME_TEAMS.get(pattern.theme_id or "other", GENERAL_TEAM)
        signal = pattern.signal

    teams: list[str] = []
    if facts is not None and facts.status in ("covered", "ambiguous"):
        teams.append(FUNDS_TEAM)
    else:
        teams.append(theme_team)

    # A recurring or rising issue also goes to the team that owns its theme.
    if signal in ("recurring", "rising") and theme_team != GENERAL_TEAM and theme_team not in teams:
        teams.append(theme_team)

    if compliance is not None and is_advice_request(compliance):
        teams.append(COMPLIANCE_TEAM)
    return teams


def priority_for(
    disposition: DispositionValue, pattern: PatternResult | None
) -> tuple[str, bool]:
    """Return the priority and whether it was raised for the theme."""
    if disposition == "HOLD_AGENT_FAILURE":
        return FAILURE_PRIORITY, False
    if pattern is None or pattern.status != "ok" or pattern.signal is None:
        return DEFAULT_PRIORITY, False
    base = SIGNAL_PRIORITY[pattern.signal]
    if pattern.theme_id in RAISED_THEMES:
        raised = RAISED[base]
        return raised, raised != base
    return base, False


def _pattern_sentence(pattern: PatternResult, priority: str, raised: bool) -> str:
    assert pattern.window is not None and pattern.signal is not None
    what = "similar reviews" if pattern.basis == "keyword_match" else "reviews in this theme"
    window = pattern.window
    text = (
        f"Pattern: {pattern.signal} on theme {pattern.theme_id}, "
        f"{pattern.count_current} {what} in the {window.days} days "
        f"{window.start.isoformat()} to {window.end.isoformat()} "
        f"and {pattern.count_prior} in the {window.days} days before. "
        f"Priority {priority}."
    )
    if raised:
        text += f" Raised one level because the theme is {pattern.theme_id}."
    if pattern.signal == "recurring":
        text += " Check whether this is a known issue."
    elif pattern.signal == "rising":
        text += " This issue is rising. Check with the owning team."
    return text


def _pii_sentence(pii: PiiCounts | None) -> str | None:
    if pii is None:
        return None
    found = [f"{name} {count}" for name, count in pii.model_dump().items() if count]
    return ("Redacted before processing: " + ", ".join(found) + ".") if found else None


def _lead(
    disposition: DispositionValue,
    facts: FactsResult | None,
    failures: tuple[AgentFailure, ...],
) -> str:
    if disposition == "SEND_READY":
        return "Reply ready for review."
    if disposition == "REFUSAL":
        return "Advice or returns request. The reply declines it."
    if disposition == "HOLD_DISAGREEMENT":
        return "Do not close this ticket."
    if disposition == "NOT_COVERED":
        if facts is not None and facts.status == "ambiguous":
            return "The scheme is unclear. The reply asks which scheme is meant."
        return "The scheme information does not cover this. The reply says so."
    parts = ", ".join(f"{f.agent} ({f.code})" for f in failures) or "unknown"
    return f"Agent failure: {parts}. No reply was drafted."


DISAGREEMENT_TEXT = (
    "Facts has a clear answer, but Pattern shows a rising issue. "
    "The reply answers the question asked and does not address the wider issue, "
    "so the ticket must not be closed."
)


def build_routing_note(
    disposition: DispositionValue,
    facts: FactsResult | None,
    pattern: PatternResult | None,
    compliance: ComplianceResult | None,
    *,
    pii_manifest: PiiCounts | None = None,
    advice_attempts: int = 0,
    extra_failures: tuple[AgentFailure, ...] = (),
) -> RoutingNote:
    """Build the note for a ticket. ``extra_failures`` names a failed drafting step."""
    failures = failed_agents(facts, pattern, compliance) + extra_failures

    priority, raised = priority_for(disposition, pattern)
    pieces = [_lead(disposition, facts, failures)]
    if pattern is not None and pattern.status == "ok" and pattern.signal is not None:
        pieces.append(_pattern_sentence(pattern, priority, raised))
    else:
        pieces.append(f"Priority {priority}.")
    pii = _pii_sentence(pii_manifest)
    if pii:
        pieces.append(pii)
    if advice_attempts > 0:
        pieces.append(f"Advice requests in this session: {advice_attempts}.")

    return RoutingNote(
        team=" + ".join(teams_for(disposition, facts, pattern, compliance)),
        priority=priority,  # type: ignore[arg-type]
        reason=" ".join(pieces),
        signals=build_signals(facts, pattern, compliance),
        disagreement=DISAGREEMENT_TEXT if disposition == "HOLD_DISAGREEMENT" else None,
    )


__all__ = [
    "COMPLIANCE_TEAM",
    "FUNDS_TEAM",
    "GENERAL_TEAM",
    "THEME_TEAMS",
    "build_routing_note",
    "build_signals",
    "compliance_signal",
    "priority_for",
    "teams_for",
]
