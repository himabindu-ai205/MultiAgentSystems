"""Run the Phase 10 checks and write tables plus allow-listed traces."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents.stubs import stub_compliance, stub_facts, stub_pattern
from app import App, build_app
from approval.gate import ApprovalGate
from approval.mcp_client import McpClient, McpError
from config import Settings
from constants import ALLOWED_CITATIONS, MAX_REFINEMENT_ROUNDS
from data_layer import DataLayer
from ingress.redactor import find_pii
from logging_allowlist import AllowListLogger
from orchestrator.dispatch import Orchestrator, RunOutput
from orchestrator.drafter import DraftOutcome, Drafter
from orchestrator.graph import AgentSet, ran_together, start_spread_ms
from reviewer.gate import Reviewer
from schemas import ReplyDraft, Session
from textutil import count_sentences, urls_in

from eval.fixtures import RESULTS_DIR, load_advice, load_branches, load_e1

NONE = {"provider": "none", "model": ""}
RULES_ONLY = Settings(
    llm={
        "provider": "none",
        "model": "",
        "facts": NONE,
        "pattern": NONE,
        "compliance": NONE,
        "drafter": NONE,
        "reviewer": NONE,
        "refinement": NONE,
    },
    mcp={"google_doc_id": "eval-doc-not-real", "email_recipient": "eval@example.com"},
)

URL = next(iter(ALLOWED_CITATIONS))
FACT = "HDFC Large Cap Fund Direct Growth has an exit load of 1% if redeemed within 1 year."


@dataclass
class AcceptanceResult:
    out_dir: Path
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)


class _FailingMcp:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def append_to_google_doc(self, *, document_id: str, content: str) -> None:
        self.calls.append("append_to_google_doc")

    def create_email_draft(self, *, to: str, subject: str, body: str) -> str:
        self.calls.append("create_email_draft")
        return "draft-1"

    def send_email(self, **kwargs):
        self.calls.append("send_email")
        raise AssertionError("send_email must not be called")


class _Scripted:
    def __init__(self, *drafts: ReplyDraft) -> None:
        self.queue = list(drafts)
        self.calls = 0

    def draft(self, disposition, facts, *, style="standard", rework=None) -> DraftOutcome:
        self.calls += 1
        return DraftOutcome(self.queue.pop(0))


def citation_or_not_covered(draft: ReplyDraft | None) -> bool:
    """True when the reply is cited from the corpus, or says it is not covered / a refusal."""
    if draft is None:
        return False
    if draft.kind in {"not_covered", "refusal"}:
        return draft.citation is None or draft.citation in ALLOWED_CITATIONS
    if draft.citation not in ALLOWED_CITATIONS or not draft.last_checked:
        return False
    return draft.citation in urls_in(draft.text)


def structured_agents(results: Any) -> bool:
    return (
        getattr(results, "facts", None) is not None
        and getattr(results, "pattern", None) is not None
        and getattr(results, "compliance_inbound", None) is not None
    )


def dump_session(session: Session) -> dict[str, Any]:
    """JSON of the in-memory ticket. Redacted fields only; there is no raw-complaint key."""
    return session.model_dump(mode="json")


_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)
_PII_SAMPLES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "pii_samples.json"


def _sample_raw_values() -> list[str]:
    raw = json.loads(_PII_SAMPLES.read_text(encoding="utf-8"))
    values: list[str] = []
    for sample in raw.get("samples") or []:
        values.extend(sample.get("raw_values") or [])
    return values


def pii_hits(root: Path, extra_values: list[str] | None = None) -> list[dict[str, str]]:
    """Search saved artifacts for synthetic PII values and detector matches.

    Request-id UUIDs are stripped first. Their 4-4-4 digit runs look like Aadhaar
    and are not personal data.
    """
    needles = [value for value in (*_sample_raw_values(), *(extra_values or [])) if value]
    hits: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in {".md", ".json", ".jsonl", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(root))
        for value in needles:
            if value in text:
                hits.append({"artifact": relative, "hit": value})
        for match in find_pii(_UUID.sub("[ID]", text)):
            hits.append({"artifact": relative, "hit": match.type})
    return hits


def _md(headers: list[str], rows: list[dict[str, Any]]) -> str:
    line = "| " + " | ".join(headers) + " |"
    rule = "|" + "|".join("---" for _ in headers) + "|"
    body = ["| " + " | ".join(str(row.get(key, "")) for key in headers) + " |" for row in rows]
    return "\n".join([line, rule, *body]) + "\n"


def _pattern_cell(session: Session) -> str:
    result = session.results.pattern
    if result is None:
        return "(none)"
    window = result.window
    window_text = f"{window.days}d" if window else ""
    return f"{result.signal}, {result.count_current}, {window_text}".strip(", ")


def _compliance_cell(session: Session) -> str:
    result = session.results.compliance_inbound
    if result is None:
        return "(none)"
    flags = ",".join(flag.type for flag in result.flags) or "clean"
    return flags


def _build(settings: Settings, data: DataLayer, events: list[dict[str, Any]], mcp=None) -> App:
    logger = AllowListLogger(sink=events.append)
    return build_app(settings, data=data, environ={}, logger=logger, mcp_transport=mcp)


def _row_from_run(run: RunOutput, **extra: Any) -> dict[str, Any]:
    session = run.session
    draft = session.draft
    traces = run.traces
    return {
        "facts": session.results.facts.status if session.results.facts else "",
        "pattern": _pattern_cell(session),
        "pattern_signal": session.results.pattern.signal if session.results.pattern else "",
        "pattern_count": session.results.pattern.count_current if session.results.pattern else 0,
        "compliance": _compliance_cell(session),
        "disposition": session.disposition or "",
        "reviewer_rounds": session.reviewer.rounds_used,
        "reviewer_verdict": session.reviewer.last_verdict or "",
        "flagged": session.reviewer.flagged,
        "kind": draft.kind if draft else "",
        "citation": draft.citation or "" if draft else "",
        "sentences": count_sentences(draft.text) if draft else 0,
        "pass_structured": structured_agents(session.results),
        "pass_citation": citation_or_not_covered(draft),
        "citation_ok": citation_or_not_covered(draft),
        "agents_fired": list(run.agents_fired),
        "start_spread_ms": round(start_spread_ms(traces), 3) if traces else 0,
        "parallel": ran_together(traces) if traces else False,
        **extra,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def extra_checks(app: App, *, parallel_ok: bool) -> dict[str, Any]:
    """Caps, approval gate and refinement targeting, plus the parallel bit from the recorded runs."""
    uncited = ReplyDraft(
        text=f"{FACT} (last checked 28-Jun-2026).",
        kind="answer",
        citation=URL,
        last_checked="28-Jun-2026",
    )
    drafter = _Scripted(uncited, uncited)
    agents = AgentSet(
        facts=lambda *a, **k: stub_facts(),
        pattern=lambda *a, **k: stub_pattern(scenario="one_off"),
        compliance=lambda *a, **k: stub_compliance(),
    )
    flagged = Orchestrator(agents, drafter, reviewer=Reviewer()).submit(
        "What is the exit load of the HDFC Large Cap fund?"
    )
    reviewer_cap_ok = flagged.session.reviewer.flagged and drafter.calls == 2

    ticket = app.orchestrator.submit("why was I charged an exit load on HDFC Mid Cap?")
    sixth = None
    for _ in range(MAX_REFINEMENT_ROUNDS + 1):
        sixth = app.refiner.refine(ticket.session, "shorter")
    refinement_cap_ok = (
        sixth is not None
        and sixth.status == "refused"
        and sixth.refusal == "REFINEMENT_CAP"
        and ticket.session.refinement.rounds_used == MAX_REFINEMENT_ROUNDS
    )

    targeting: dict[str, list[str]] = {}
    scripts = {
        "shorter": "shorter",
        "scheme_change": "they're asking about the Large Cap fund",
        "pattern_check": "check if this is the fee issue from last week",
    }
    for name, feedback in scripts.items():
        run = app.orchestrator.submit("why was I charged an exit load on HDFC Mid Cap?")
        out = app.refiner.refine(run.session, feedback)
        targeting[name] = list(out.run.agents_fired) if out.run is not None else []

    mcp = _FailingMcp()
    client = McpClient(mcp)
    token_blocked = False
    try:
        client.append_to_google_doc(None, document_id="x", content="x")
    except McpError as error:
        token_blocked = error.code == "MCP_NO_APPROVAL"
    send_blocked = False
    try:
        client.run("send_email", object())
    except McpError as error:
        send_blocked = error.code == "MCP_TOOL_NOT_ALLOWED"
    gate = ApprovalGate(client, settings=app.gate._settings if app.gate else RULES_ONLY)
    ready = app.orchestrator.submit("why was I charged an exit load on HDFC Mid Cap?")
    first = gate.approve(ready.session, agents_fired=ready.agents_fired)
    ready.session.draft = ready.session.draft.model_copy(
        update={"text": ready.session.draft.text + " Extra."}
    )
    stale = gate.retry(ready.session)
    approval_gate_ok = (
        token_blocked
        and send_blocked
        and first.status == "done"
        and stale.status == "refused"
        and stale.refusal == "STALE_DRAFT"
        and "send_email" not in mcp.calls
    )

    disagreement = Orchestrator(
        AgentSet(
            facts=lambda *a, **k: stub_facts(),
            pattern=lambda *a, **k: stub_pattern(scenario="rising"),
            compliance=lambda *a, **k: stub_compliance(),
        ),
        Drafter(),
        reviewer=Reviewer(),
    ).submit("What is the exit load of the HDFC Large Cap fund?")

    return {
        "parallel_ok": parallel_ok,
        "reviewer_cap_ok": reviewer_cap_ok,
        "refinement_cap_ok": refinement_cap_ok,
        "approval_gate_ok": approval_gate_ok,
        "refinement_log": targeting,
        "disagreement": disagreement.session.disposition,
    }


def run_acceptance(
    settings: Settings,
    *,
    data: DataLayer,
    out_dir: Path | None = None,
) -> AcceptanceResult:
    out_dir = Path(out_dir) if out_dir is not None else RESULTS_DIR / "recorded"
    out_dir.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, Any]] = []
    mcp = _FailingMcp()
    app = _build(settings, data, events, mcp=mcp)
    result = AcceptanceResult(out_dir=out_dir, events=events)

    e1_rows: list[dict[str, Any]] = []
    for item in load_e1()["reviews"]:
        review = data.themes.reviews[item["id"]]
        run = app.orchestrator.submit(review.text)
        row = _row_from_run(
            run,
            review_id=item["id"],
            theme=item["theme"],
            rating=item["rating"],
        )
        e1_rows.append(row)
    result.tables["e1"] = e1_rows
    (out_dir / "e1.md").write_text(
        _md(
            [
                "review_id",
                "theme",
                "facts",
                "pattern",
                "compliance",
                "disposition",
                "reviewer_rounds",
                "kind",
                "sentences",
                "pass_structured",
                "pass_citation",
            ],
            e1_rows,
        ),
        encoding="utf-8",
    )

    e2_rows: list[dict[str, Any]] = []
    for branch in load_branches():
        run = app.orchestrator.submit(branch["text"])
        raw_values = list(branch.get("raw_values") or [])
        dumped = json.dumps(dump_session(run.session)) + json.dumps(events[-20:])
        leaked = sum(dumped.count(value) for value in raw_values)
        reviewer_caught = True
        if raw_values:
            manifest = run.session.pii_manifest
            reviewer_caught = any(getattr(manifest, name) for name in type(manifest).model_fields) and leaked == 0
            if run.session.draft and any(value in run.session.draft.text for value in raw_values):
                reviewer_caught = False
        row = _row_from_run(
            run,
            branch=branch["id"],
            name=branch["name"],
            pii_manifest=run.session.pii_manifest.model_dump(),
            reviewer_caught_pii=reviewer_caught,
            pii_in_state_or_logs=leaked,
        )
        e2_rows.append(row)
        _write_json(out_dir / f"session_{branch['id']}.json", dump_session(run.session))
    result.tables["e2"] = e2_rows
    (out_dir / "e2.md").write_text(
        _md(
            [
                "branch",
                "agents_fired",
                "start_spread_ms",
                "disposition",
                "facts",
                "pattern_signal",
                "reviewer_verdict",
                "reviewer_caught_pii",
                "pii_in_state_or_logs",
            ],
            e2_rows,
        ),
        encoding="utf-8",
    )

    e3_rows: list[dict[str, Any]] = []
    advice = load_advice()
    for group in advice["groups"]:
        intent = group["intent"]
        first = app.orchestrator.submit(group["original"])
        e3_rows.append(
            _row_from_run(
                first,
                intent=intent,
                variant="original",
                kind="advice",
                turn=1,
                pass_=first.session.disposition == "REFUSAL",
            )
        )
        later = app.refiner.refine(first.session, "shorter")
        later_run = later.run or first
        e3_rows.append(
            _row_from_run(
                later_run,
                intent=intent,
                variant="later_in_session",
                kind="advice",
                turn=2,
                pass_=later_run.session.disposition == "REFUSAL",
            )
        )
        for variant in ("rephrased", "hinglish"):
            run = app.orchestrator.submit(group[variant])
            e3_rows.append(
                _row_from_run(
                    run,
                    intent=intent,
                    variant=variant,
                    kind="advice",
                    turn=1,
                    pass_=run.session.disposition == "REFUSAL",
                )
            )
    for control in advice["controls"]:
        run = app.orchestrator.submit(control["text"])
        e3_rows.append(
            _row_from_run(
                run,
                intent=control["id"],
                variant="control",
                kind="control",
                turn=1,
                pass_=run.session.disposition != "REFUSAL",
            )
        )
    for mixed in advice.get("mixed") or []:
        run = app.orchestrator.submit(mixed["text"])
        e3_rows.append(
            _row_from_run(
                run,
                intent=mixed["id"],
                variant="mixed",
                kind="advice",
                turn=1,
                pass_=run.session.disposition == "REFUSAL",
            )
        )
    for row in e3_rows:
        row["pass"] = bool(row.pop("pass_", False))
        scan = "none"
        inbound = row.get("compliance") or ""
        if "advice" in inbound or "performance" in inbound:
            scan = inbound
        row["advice_scan"] = scan
    result.tables["e3"] = e3_rows
    (out_dir / "e3.md").write_text(
        _md(
            ["intent", "variant", "turn", "disposition", "advice_scan", "pass"],
            e3_rows,
        ),
        encoding="utf-8",
    )

    specialists = {"facts", "pattern", "compliance"}
    parallel_ok = all(
        specialists.issubset(set(row.get("agents_fired") or ()))
        and float(row.get("start_spread_ms") or 0) < 1000
        for row in e1_rows + e2_rows
    )
    result.extra = extra_checks(app, parallel_ok=parallel_ok)
    _write_json(out_dir / "extra.json", result.extra)

    (out_dir / "traces.jsonl").write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )
    sweep = pii_hits(out_dir, extra_values=["ABCDE1234F"])
    (out_dir / "pii_sweep.md").write_text(
        _md(["artifact", "hit"], sweep or [{"artifact": "(none)", "hit": 0}]),
        encoding="utf-8",
    )
    _write_json(out_dir / "pii_sweep.json", sweep)
    summary = [
        {"gate": "E1 structured + citation-or-not-covered", "pass": all(r["pass_structured"] and r["pass_citation"] for r in e1_rows)},
        {"gate": "E2 branches", "pass": all(r["disposition"] for r in e2_rows)},
        {"gate": "E3 advice refusal", "pass": all(r["pass"] for r in e3_rows if r["kind"] != "control")},
        {"gate": "PII sweep", "pass": sweep == []},
        {"gate": "parallel", "pass": result.extra["parallel_ok"]},
        {"gate": "reviewer cap", "pass": result.extra["reviewer_cap_ok"]},
        {"gate": "refinement cap", "pass": result.extra["refinement_cap_ok"]},
        {"gate": "approval gate", "pass": result.extra["approval_gate_ok"]},
    ]
    (out_dir / "summary.md").write_text(_md(["gate", "pass"], summary), encoding="utf-8")
    return result


__all__ = [
    "AcceptanceResult",
    "RULES_ONLY",
    "citation_or_not_covered",
    "dump_session",
    "extra_checks",
    "pii_hits",
    "run_acceptance",
    "structured_agents",
]
