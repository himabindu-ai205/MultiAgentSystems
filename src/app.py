"""The human-view entry point (Implementation Plan Phase 8, Architecture sections 10–11).

One in-memory ticket at a time. The person sees the redacted complaint, the draft, the
citation, the last-checked date, the routing note, the Reviewer verdict and any flag, and
the agents that ran. They can refine (with the remaining count) or discard. Approve is
offered only for a draft the Reviewer passed; a flagged draft has no approve control.

Approve creates a record bound to the draft hash, appends it to the support Google Doc,
then creates a Gmail draft. Nothing is sent. A flagged draft has no approve control.

Nothing here keeps the raw complaint or the raw feedback. Both are redacted before they
reach a model, a log or the session, and the view only prints what the session holds.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from agents.compliance import ComplianceAgent
from agents.facts import FactsAgent
from agents.pattern import PatternAgent
from approval.gate import ApprovalGate
from approval.mcp_client import McpClient, McpTransport, UnconfiguredTransport
from config import Settings, load_settings
from constants import MAX_REFINEMENT_ROUNDS
from data_layer import DataLayer, load_all
from ingress.redactor import IngressError
from llm import env as llm_env
from llm.gemini import Transport
from llm.groq import Transport as GroqTransport
from llm.groq import build_groq_uses
from logging_allowlist import AllowListLogger
from orchestrator.dispatch import Orchestrator, RunOutput
from orchestrator.drafter import Drafter
from orchestrator.graph import AgentSet
from refinement.build import build_router
from refinement.refiner import Refiner
from refinement.view import APPROVE, DISCARD, REFINE, RETRY, build_view, render
from reviewer.build import build_reviewer
from schemas import Session

Read = Callable[[str], str]
Write = Callable[[str], None]
Approve = Callable[[Session], str]

ASK_COMPLAINT = "Paste the customer complaint, then press Enter."
WEB_UNAVAILABLE = "Only the command-line form is available."
EMPTY_REFINE = "Type what to change after r, for example: r shorter"
UNKNOWN_COMMAND = "Unknown command. Use: r <what to change>, a (approve), d (discard), q (quit)"
APPROVE_UNAVAILABLE = "Approve isn't available for this draft."
APPROVE_UNCONNECTED = "Approve isn't connected yet. You can still refine or discard."
DISCARDED = "The ticket was discarded. Nothing was saved."
NOTHING_SAVED = "Nothing was saved."

_REFINE = {"r", REFINE}
_APPROVE = {"a", APPROVE}
_DISCARD = {"d", DISCARD}
_QUIT = {"q", "quit"}


@dataclass
class App:
    """The running ticket: an Orchestrator, the Refiner, and the approval gate."""

    orchestrator: Orchestrator
    refiner: Refiner
    max_rounds: int = MAX_REFINEMENT_ROUNDS
    gate: ApprovalGate | None = None


def build_app(
    settings: Settings,
    *,
    data: DataLayer | None = None,
    environ: Mapping[str, str] | None = None,
    transport: Transport | None = None,
    groq_transport: GroqTransport | None = None,
    mcp_transport: McpTransport | None = None,
    logger: AllowListLogger | None = None,
) -> App:
    """The real agents, Reviewer and refinement router, wired from ``settings``."""
    loaded = data if data is not None else load_all(settings.data_path)
    if environ is None:
        # Looked up on the module so a test can replace ``llm.env.environment_with_env_file``.
        environ = llm_env.environment_with_env_file()
    log = logger or AllowListLogger()
    timeout = settings.limits.agent_timeout_seconds
    max_sentences = settings.limits.max_reply_sentences
    max_rounds = settings.limits.refinement_rounds

    groq = build_groq_uses(
        settings, environ=environ, transport=groq_transport, timeout_seconds=timeout
    )
    facts = FactsAgent(loaded.corpus, classifier=groq.facts)
    pattern = PatternAgent(loaded.themes, settings=settings.pattern, classifier=groq.pattern)
    compliance = ComplianceAgent(corpus=loaded.corpus, classifier=groq.compliance)
    drafter = Drafter(corpus=loaded.corpus, writer=groq.drafter, max_sentences=max_sentences)
    reviewer = build_reviewer(
        settings, corpus=loaded.corpus, logger=log, environ=environ, transport=transport
    )
    orchestrator = Orchestrator(
        AgentSet.from_agents(facts, pattern, compliance),
        drafter,
        logger=log,
        reviewer=reviewer,
        timeout_seconds=timeout,
        max_complaint_chars=settings.limits.max_complaint_chars,
    )
    router = build_router(settings, environ=environ, transport=transport)
    refiner = Refiner(orchestrator, router, logger=log, max_rounds=max_rounds)
    gate = ApprovalGate(
        McpClient(mcp_transport or UnconfiguredTransport()),
        settings=settings,
        logger=log,
    )
    return App(orchestrator, refiner, max_rounds=max_rounds, gate=gate)


def run_session(
    app: App,
    complaint: str,
    *,
    read: Read,
    write: Write,
    approve: Approve | None = None,
) -> Session:
    """Show one ticket and take refine / approve / discard until the person stops."""
    run: RunOutput | None = app.orchestrator.submit(complaint)
    session = run.session
    notice: str | None = None

    while True:
        view = build_view(session, run=run, notice=notice, max_rounds=app.max_rounds)
        write(render(view))
        notice = None
        try:
            line = read("> ")
        except EOFError:
            write(NOTHING_SAVED)
            return session

        command, _, rest = line.strip().partition(" ")
        command = command.lower()
        if command == "":
            continue
        if command in _QUIT:
            write(NOTHING_SAVED)
            return session
        if command in _DISCARD:
            app.refiner.discard(session)
            write(DISCARDED)
            return session
        if command in _APPROVE:
            if RETRY in view.controls:
                if app.gate is None:
                    write(APPROVE_UNCONNECTED)
                    continue
                try:
                    result = app.gate.retry(session)
                except RuntimeError as error:
                    write(str(error))
                    continue
                write(result.message)
                if result.status in {"done", "idempotent"}:
                    return session
                continue
            if APPROVE not in view.controls:
                write(APPROVE_UNAVAILABLE)
                continue
            if approve is not None:
                write(approve(session))
                return session
            if app.gate is None:
                write(APPROVE_UNCONNECTED)
                continue
            fired = run.agents_fired if run is not None else session.approval.agents_fired
            try:
                result = app.gate.approve(session, agents_fired=fired)
            except RuntimeError as error:
                write(str(error))
                continue
            write(result.message)
            if result.status in {"done", "idempotent"}:
                return session
            continue
        if command in _REFINE:
            feedback = rest.strip()
            if not feedback:
                write(EMPTY_REFINE)
                continue
            result = app.refiner.refine(session, feedback)
            if result.status == "refused":
                write(result.message or "")
                continue
            run = result.run
            notice = result.message
            continue
        write(UNKNOWN_COMMAND)


def main(
    argv: Sequence[str] | None = None,
    *,
    read: Read = input,
    write: Write = print,
    app: App | None = None,
    settings: Settings | None = None,
    approve: Approve | None = None,
) -> int:
    """Run one ticket from the command line. Returns a process exit code."""
    chosen = settings or load_settings()
    if chosen.ui.form != "cli":
        write(WEB_UNAVAILABLE)
        return 1
    if app is None:
        try:
            app = build_app(chosen)
        except (RuntimeError, ValueError) as error:
            write(str(error))
            return 1

    words = list(sys.argv[1:] if argv is None else argv)
    if words:
        complaint = " ".join(words)
    else:
        write(ASK_COMPLAINT)
        try:
            complaint = read("Complaint: ")
        except EOFError:
            complaint = ""

    try:
        run_session(app, complaint, read=read, write=write, approve=approve)
    except IngressError as error:
        write(str(error))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
