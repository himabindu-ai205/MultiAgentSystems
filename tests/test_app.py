"""Phase 8: the command-line human view. Show the ticket, take refine / approve / discard."""

from __future__ import annotations

import json

import pytest

from agents.stubs import stub_compliance, stub_facts, stub_pattern
from app import App, build_app, main, run_session
from config import Settings
from constants import MAX_REFINEMENT_ROUNDS, SCHEME_SOURCE_URLS
from orchestrator.dispatch import Orchestrator
from orchestrator.drafter import DraftOutcome, Drafter
from orchestrator.graph import AgentSet
from refinement.refiner import Refiner
from refinement.router import RefinementRouter
from reviewer.gate import Reviewer
from schemas import ReplyDraft

QUESTION = "What is the exit load of the HDFC Large Cap fund?"
URL = SCHEME_SOURCE_URLS["large_cap"]
FACT = "HDFC Large Cap Fund Direct Growth has an exit load of 1% if redeemed within 1 year."
SOURCE = f"Source: {URL} (last checked 28-Jun-2026)."
ADVICE = ReplyDraft(
    text=f"{FACT} You should buy more units. {SOURCE}", kind="answer", citation=URL, last_checked="28-Jun-2026"
)
KEY = "test-key-DO-NOT-LEAK-123"


class Scripted:
    def __init__(self, *drafts):
        self.queue = list(drafts)

    def draft(self, disposition, facts, *, style="standard", rework=None):
        return DraftOutcome(self.queue.pop(0))


def stub_app(drafter=None) -> App:
    agents = AgentSet(
        facts=lambda *a, **k: stub_facts(),
        pattern=lambda *a, **k: stub_pattern(scenario="one_off"),
        compliance=lambda *a, **k: stub_compliance(),
    )
    orchestrator = Orchestrator(agents, drafter or Drafter(), reviewer=Reviewer())
    return App(orchestrator, Refiner(orchestrator, RefinementRouter()))


class Console:
    """Scripted input and captured output."""

    def __init__(self, *lines: str) -> None:
        self.lines = list(lines)
        self.out: list[str] = []

    def read(self, prompt: str = "") -> str:
        if not self.lines:
            raise EOFError
        return self.lines.pop(0)

    def write(self, text: str = "") -> None:
        self.out.append(str(text))

    @property
    def shown(self) -> str:
        return "\n".join(self.out)


def play(*inputs: str, app: App | None = None, complaint: str = QUESTION, **kwargs):
    console = Console(*inputs)
    session = run_session(app or stub_app(), complaint, read=console.read, write=console.write, **kwargs)
    return session, console


# ---------------------------------------------------------------------------
# A session
# ---------------------------------------------------------------------------


def test_the_first_screen_shows_the_ticket_and_the_controls():
    session, console = play("d")
    assert QUESTION in console.shown and URL in console.shown and "PASS" in console.shown
    assert "[r] Refine (5 left)" in console.shown and "[a] Approve" in console.shown and "[d] Discard" in console.shown
    assert session.draft is not None


def test_refine_shows_the_new_draft_with_one_fewer_refinement_left():
    session, console = play("r shorter", "d")
    assert session.refinement.rounds_used == 1
    assert "Refine (4 left)" in console.shown
    assert console.shown.count("Reviewer: PASS") == 2


def test_refine_with_the_long_command_name():
    session, _ = play("refine shorter", "discard")
    assert session.refinement.rounds_used == 1


def test_refine_with_no_words_asks_what_to_change_and_uses_no_round():
    session, console = play("r", "d")
    assert session.refinement.rounds_used == 0
    assert "what to change" in console.shown.lower()


def test_an_unclear_refinement_shows_the_router_notice():
    _, console = play("r hmm", "d")
    assert "rephrase" in console.shown.lower()


def test_discard_ends_the_session_and_keeps_nothing():
    session, console = play("d")
    assert session.approval.status == "rejected"
    assert "discarded" in console.shown.lower() and "nothing was saved" in console.shown.lower()


def test_leaving_without_a_choice_saves_nothing_and_leaves_the_ticket_pending():
    for leave in (["q"], []):  # a quit command, and the input ending
        session, console = play(*leave)
        assert session.approval.status == "pending"
        assert "nothing was saved" in console.shown.lower()


def test_an_unknown_command_shows_the_help_and_carries_on():
    session, console = play("what", "d")
    assert "r <" in console.shown and session.approval.status == "rejected"


def test_the_sixth_refinement_is_refused_with_a_message_and_the_session_goes_on():
    inputs = ["r shorter"] * (MAX_REFINEMENT_ROUNDS + 1) + ["d"]
    session, console = play(*inputs)
    assert session.refinement.rounds_used == MAX_REFINEMENT_ROUNDS
    assert "limit" in console.shown.lower()
    assert "Refinement limit reached." in console.shown


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


def test_approve_is_not_connected_until_the_approval_gate_exists():
    session, console = play("a", "d")
    assert "isn't connected" in console.shown
    assert session.approval.status == "rejected"  # it went on to the discard


def test_approve_calls_the_gate_when_one_is_given_and_ends_the_session():
    seen = []

    def approve(session):
        seen.append(session.request_id)
        return "Approved and saved."

    session, console = play("a", approve=approve)
    assert seen == [session.request_id] and "Approved and saved." in console.shown


def test_approve_through_the_gate_writes_the_doc_and_email_draft_and_never_sends():
    from approval.gate import ApprovalGate
    from approval.mcp_client import McpClient
    from test_approval import FakeMcp, mcp_settings

    mcp = FakeMcp()
    app = stub_app()
    app.gate = ApprovalGate(McpClient(mcp), settings=mcp_settings())
    session, console = play("a", app=app)
    assert session.approval.status == "approved"
    assert "not sent" in console.shown.lower()
    assert [c[0] for c in mcp.calls] == ["append_to_google_doc", "create_email_draft"]
    assert "send_email" not in [c[0] for c in mcp.calls]


def test_a_retry_after_a_failed_append_finishes_the_write_and_never_sends():
    from approval.gate import ApprovalGate
    from approval.mcp_client import McpClient, McpError
    from test_approval import FakeMcp, mcp_settings

    class FailFirstAppend(FakeMcp):
        def append_to_google_doc(self, *, document_id: str, content: str) -> None:
            already = [c for c in self.calls if c[0] == "append_to_google_doc"]
            self.calls.append(("append_to_google_doc", {"document_id": document_id, "content": content}))
            if not already:
                raise McpError("MCP_APPEND_FAILED")

    mcp = FailFirstAppend()
    app = stub_app()
    app.gate = ApprovalGate(McpClient(mcp), settings=mcp_settings())
    session, console = play("a", "a", app=app)
    assert session.approval.status == "approved"
    assert session.approval.mcp_tools_called == ("append_to_google_doc", "create_email_draft")
    assert "Retry" in console.shown
    assert "not sent" in console.shown.lower()
    assert [c[0] for c in mcp.calls] == [
        "append_to_google_doc",
        "append_to_google_doc",
        "create_email_draft",
    ]
    assert "send_email" not in [c[0] for c in mcp.calls]


def test_empty_mcp_settings_show_the_error_and_leave_the_ticket_open():
    from approval.gate import ApprovalGate
    from approval.mcp_client import McpClient
    from test_approval import FakeMcp

    mcp = FakeMcp()
    app = stub_app()
    app.gate = ApprovalGate(McpClient(mcp), settings=Settings())
    session, console = play("a", "d", app=app)
    assert "mcp.google_doc_id" in console.shown
    assert mcp.calls == []
    assert session.approval.status == "rejected"


def test_discard_with_the_gate_attached_never_calls_mcp():
    from approval.gate import ApprovalGate
    from approval.mcp_client import McpClient
    from test_approval import FakeMcp, mcp_settings

    mcp = FakeMcp()
    app = stub_app()
    app.gate = ApprovalGate(McpClient(mcp), settings=mcp_settings())
    session, console = play("d", app=app)
    assert session.approval.status == "rejected"
    assert mcp.calls == []
    assert "discarded" in console.shown.lower()


def test_a_flagged_draft_cannot_be_approved_even_if_asked():
    app = stub_app(Scripted(ADVICE, ADVICE))
    called = []
    session, console = play("a", "d", app=app, approve=lambda s: called.append(s) or "no")
    assert session.reviewer.flagged
    assert "[a] Approve" not in console.shown
    assert "isn't available" in console.shown
    assert called == []


# ---------------------------------------------------------------------------
# Raw text, bad input
# ---------------------------------------------------------------------------


def test_the_raw_complaint_and_the_raw_feedback_are_never_printed():
    session, console = play(
        "r shorter, my PAN is FGHIJ5678K zebra",
        "d",
        complaint="my PAN is ABCDE1234F, what is the exit load of the HDFC Large Cap fund?",
    )
    assert "ABCDE1234F" not in console.shown and "FGHIJ5678K" not in console.shown
    assert "zebra" not in console.shown and "[PAN]" in console.shown


def test_main_refuses_an_unusable_complaint_without_starting_any_agent():
    started = []
    agents = AgentSet(
        facts=lambda *a, **k: started.append("facts") or stub_facts(),
        pattern=lambda *a, **k: started.append("pattern") or stub_pattern(),
        compliance=lambda *a, **k: started.append("compliance") or stub_compliance(),
    )
    orchestrator = Orchestrator(agents, Drafter(), reviewer=Reviewer())
    app = App(orchestrator, Refiner(orchestrator, RefinementRouter()))
    console = Console("   ")

    code = main([], read=console.read, write=console.write, app=app)

    assert code == 2 and started == []
    assert console.shown.strip()  # a fixed message, not blank


def test_main_takes_the_complaint_from_the_command_line_words():
    console = Console("d")
    code = main(["what", "is", "the", "exit", "load", "of", "the", "HDFC", "Large", "Cap", "fund?"],
                read=console.read, write=console.write, app=stub_app())
    assert code == 0 and "Large Cap" in console.shown


def test_main_asks_for_the_complaint_when_none_is_given():
    console = Console(QUESTION, "d")
    assert main([], read=console.read, write=console.write, app=stub_app()) == 0
    assert URL in console.shown


def test_only_the_command_line_form_exists():
    console = Console()
    code = main([], read=console.read, write=console.write, settings=Settings(ui={"form": "web"}))
    assert code == 1 and "command-line" in console.shown.lower()


def test_main_reports_a_missing_key_as_a_clear_message_not_a_traceback(monkeypatch):
    monkeypatch.setattr("llm.env.environment_with_env_file", lambda: {})
    console = Console()
    settings = Settings(llm={"reviewer": {"provider": "gemini", "model": "gemini-test-1"}})
    code = main([], read=console.read, write=console.write, settings=settings)
    assert code == 1 and "GOOGLE_API_KEY" in console.shown


# ---------------------------------------------------------------------------
# The real stack
# ---------------------------------------------------------------------------


class Transport:
    def __init__(self, label: str) -> None:
        self.label, self.requests = label, []

    def __call__(self, url, headers, body, timeout):
        self.requests.append(json.loads(body))
        return 200, json.dumps({"candidates": [{"content": {"parts": [{"text": self.label}]}, "finishReason": "STOP"}]}).encode()


def rules_only() -> Settings:
    return Settings(llm={"reviewer": {"provider": "none"}, "refinement": {"provider": "none"}})


def test_the_real_agents_answer_and_a_refinement_re_runs_only_what_it_should(data):
    app = build_app(rules_only(), data=data)
    session, console = play("r they are asking about the Mid Cap fund", "d", app=app)

    assert session.results.facts.scheme_id == "mid_cap"
    assert SCHEME_SOURCE_URLS["mid_cap"] in console.shown
    assert session.refinement.history[0].agents_fired == ("facts", "orchestrator")
    assert session.refinement.history[0].agents_cached == ("pattern", "compliance")


def test_the_real_stack_sends_the_reviewer_check_and_free_wording_to_gemini_with_the_key(data):
    transport = Transport("none")
    settings = Settings(
        llm={
            "reviewer": {"provider": "gemini", "model": "gemini-test-1"},
            "refinement": {"provider": "gemini", "model": "gemini-test-1"},
        }
    )
    app = build_app(settings, data=data, environ={"GOOGLE_API_KEY": KEY}, transport=transport)
    session, console = play("r could you take another look from a new angle", "d", app=app)

    assert len(transport.requests) >= 2  # the Reviewer's outbound check, and the feedback label
    assert KEY not in console.shown and KEY not in repr(session)
    assert session.refinement.history[0].category == "unclear"  # the label said "none", which is not a label


def test_the_app_uses_the_settings_caps(data):
    settings = Settings(
        llm={"reviewer": {"provider": "none"}, "refinement": {"provider": "none"}},
        limits={"refinement_rounds": 2},
    )
    session, console = play("r shorter", "r shorter", "r shorter", "d", app=build_app(settings, data=data))
    assert session.refinement.rounds_used == 2
    assert "limit" in console.shown.lower()


def test_building_the_app_with_gemini_and_no_key_fails_clearly(data):
    settings = Settings(llm={"reviewer": {"provider": "none"}, "refinement": {"provider": "gemini", "model": "m"}})
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        build_app(settings, data=data, environ={})
