"""A plain-text walkthrough of one Orchestrator run.

Used by ``scripts/verify_flow.py`` so a person can see redaction, the three agents
starting together, the merge, the draft and the Reviewer, without raw PII. The report
only prints what the session already holds.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from orchestrator.dispatch import RunOutput
from orchestrator.graph import NODE_NAMES, NodeTrace, ran_together, start_spread_ms
from schemas import ComplianceResult, FactsResult, PatternResult, PiiCounts, Session

# The complaint used to walk the full stack by hand.
EXIT_LOAD_QUESTION = (
    "why was I charged an exit load, what was the amount and why I was charged?"
)
EXIT_LOAD_NAMED = (
    "why was I charged an exit load on the HDFC Large Cap fund, "
    "what was the amount and why I was charged?"
)


def _pii_line(counts: PiiCounts) -> str:
    found = [f"{name}={getattr(counts, name)}" for name in type(counts).model_fields if getattr(counts, name)]
    return ", ".join(found) if found else "(none)"


def _ms(trace: NodeTrace) -> float:
    return (trace.ended_at - trace.started_at).total_seconds() * 1000


def _parallel(traces: Mapping[str, NodeTrace]) -> list[str]:
    if not traces:
        return ["  (no specialist ran this time; cached results were reused)"]
    first = min(t.started_at for t in traces.values())
    spread = start_spread_ms(traces)
    overlapped = "yes" if ran_together(traces) else "no"
    lines = [f"Parallel start: spread {spread:.1f} ms, overlapped={overlapped}"]
    for node in NODE_NAMES:
        trace = traces.get(node)
        if trace is None:
            continue
        offset = (trace.started_at - first).total_seconds() * 1000
        extra = f"  error={trace.error_code}" if trace.error_code else ""
        lines.append(
            f"  {trace.agent:<12} +{offset:6.1f} ms   { _ms(trace):6.1f} ms  {trace.status}{extra}"
        )
    return lines


def _facts(result: FactsResult | None) -> list[str]:
    if result is None:
        return ["Facts: (missing)"]
    lines = [f"Facts: status={result.status}"]
    if result.scheme_id:
        lines.append(f"  scheme={result.scheme_id}  section={result.section_title}")
    if result.answer:
        lines.append(f"  answer: {result.answer}")
    if result.citation:
        lines.append(f"  citation: {result.citation}")
        lines.append(f"  source_fetched_at: {result.source_fetched_at}")
    if result.candidates:
        lines.append("  candidates: " + ", ".join(result.candidates))
    if result.searched.schemes or result.searched.sections:
        lines.append(
            "  searched: schemes="
            + ",".join(result.searched.schemes)
            + "  sections="
            + ",".join(result.searched.sections)
        )
    if result.error:
        lines.append(f"  error={result.error}")
    return lines


def _pattern(result: PatternResult | None) -> list[str]:
    if result is None:
        return ["Pattern: (missing)"]
    lines = [f"Pattern: status={result.status}"]
    if result.status == "ok":
        window = result.window
        window_s = f"{window.start}..{window.end} ({window.days}d)" if window else "(none)"
        lines.append(
            f"  theme={result.theme_id}  signal={result.signal}  "
            f"count={result.count_current}/{result.count_prior}  basis={result.basis}"
        )
        lines.append(f"  window: {window_s}  matched_ids={len(result.matched_review_ids)}")
    if result.error:
        lines.append(f"  error={result.error}")
    return lines


def _compliance(result: ComplianceResult | None) -> list[str]:
    if result is None:
        return ["Compliance: (missing)"]
    flags = ", ".join(f"{f.type}:{f.detail}" for f in result.flags) or "(none)"
    lines = [
        f"Compliance: status={result.status}  mode={result.mode}  clean={result.clean}",
        f"  flags: {flags}",
        f"  pii_redacted: {_pii_line(result.pii_redacted)}",
    ]
    if result.error:
        lines.append(f"  error={result.error}")
    return lines


def _draft(session: Session, run: RunOutput) -> list[str]:
    draft = session.draft
    if draft is None:
        return ["Draft: (none)"]
    return [
        f"Draft: kind={draft.kind}  wording={run.outcome.wording}",
        f"  {draft.text}",
        f"  citation: {draft.citation or '(none)'}",
        f"  last_checked: {draft.last_checked or '(none)'}",
    ]


def _review(run: RunOutput) -> list[str]:
    review = run.review
    state = run.session.reviewer
    if review is None:
        return [f"Reviewer: {state.last_verdict or 'NOT REVIEWED'}  flagged={state.flagged}"]
    failures = ", ".join(review.failure_types) or "(none)"
    lines = [
        f"Reviewer: {review.verdict}  round={review.round}  flagged={review.flagged}",
        f"  failures: {failures}",
    ]
    if review.instruction:
        lines.append(f"  instruction: {review.instruction}")
    return lines


def format_run(
    run: RunOutput,
    *,
    title: str | None = None,
    events: Sequence[Mapping[str, object]] = (),
) -> str:
    """Plain text for one ticket. Prints redacted_text, never the raw complaint."""
    session = run.session
    note = session.routing_note
    lines: list[str] = []
    if title:
        lines += [title, "=" * len(title)]
    lines += [
        f"Ticket {session.request_id}",
        f"Redacted complaint: {session.redacted_text}",
        f"PII counts: {_pii_line(session.pii_manifest)}",
        "",
        *_parallel(run.traces),
        "",
        *_facts(session.results.facts),
        "",
        *_pattern(session.results.pattern),
        "",
        *_compliance(session.results.compliance_inbound),
        "",
        f"Disposition: {session.disposition}",
    ]
    if note:
        lines.append(f"Routing: {note.team}  priority={note.priority}")
        lines.append(f"  {note.reason}")
        if note.disagreement:
            lines.append(f"  disagreement: {note.disagreement}")
    lines += ["", *_draft(session, run), "", *_review(run)]
    lines.append("")
    lines.append("Agents fired: " + ", ".join(run.agents_fired))
    if run.agents_cached:
        lines.append("Agents cached: " + ", ".join(run.agents_cached))
    kinds = [str(e.get("event")) for e in events]
    if kinds:
        lines.append("Log events: " + ", ".join(kinds))
    return "\n".join(lines)


__all__ = ["EXIT_LOAD_NAMED", "EXIT_LOAD_QUESTION", "format_run"]
