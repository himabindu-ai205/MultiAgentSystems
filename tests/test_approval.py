"""Phase 9: nothing moves without an explicit approve, and nothing is ever sent."""

from __future__ import annotations

import pytest

from agents.stubs import stub_compliance, stub_facts, stub_pattern
from approval.gate import ApprovalGate, draft_hash
from approval.mcp_client import McpClient, McpError
from config import Settings
from constants import MCP_TOOLS, SCHEME_SOURCE_URLS
from logging_allowlist import AllowListLogger
from orchestrator.dispatch import Orchestrator
from orchestrator.drafter import DraftOutcome, Drafter
from orchestrator.graph import AgentSet
from reviewer.gate import Reviewer
from schemas import ReplyDraft

QUESTION = "What is the exit load of the HDFC Large Cap fund?"
URL = SCHEME_SOURCE_URLS["large_cap"]
FACT = "HDFC Large Cap Fund Direct Growth has an exit load of 1% if redeemed within 1 year."
SOURCE = f"Source: {URL} (last checked 28-Jun-2026)."
GOOD = ReplyDraft(text=f"{FACT} {SOURCE}", kind="answer", citation=URL, last_checked="28-Jun-2026")
ADVICE = ReplyDraft(
    text=f"{FACT} You should buy more units. {SOURCE}", kind="answer", citation=URL, last_checked="28-Jun-2026"
)
PAN = "ABCDE1234F"
DOC = "doc-test-not-real"
MAIL = "mf-support@example.com"


class Scripted:
    def __init__(self, *drafts):
        self.queue = list(drafts)

    def draft(self, disposition, facts, *, style="standard", rework=None):
        return DraftOutcome(self.queue.pop(0))


class FakeMcp:
    """Records calls. Never sends mail. Can fail a named tool."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail: str | None = None

    def append_to_google_doc(self, *, document_id: str, content: str) -> None:
        self.calls.append(("append_to_google_doc", {"document_id": document_id, "content": content}))
        if self.fail == "append_to_google_doc":
            raise McpError("MCP_APPEND_FAILED")

    def create_email_draft(self, *, to: str, subject: str, body: str) -> str:
        self.calls.append(("create_email_draft", {"to": to, "subject": subject, "body": body}))
        if self.fail == "create_email_draft":
            raise McpError("MCP_DRAFT_FAILED")
        return "draft-1"

    def send_email(self, **kwargs):  # exists on the fake server, must never be reached
        self.calls.append(("send_email", kwargs))
        raise AssertionError("send_email must not be called")


def mcp_settings() -> Settings:
    return Settings(mcp={"google_doc_id": DOC, "email_recipient": MAIL})


def events():
    sink: list[dict] = []
    return sink, AllowListLogger(sink=sink.append)


def stack(drafter=None):
    agents = AgentSet(
        facts=lambda *a, **k: stub_facts(),
        pattern=lambda *a, **k: stub_pattern(scenario="one_off"),
        compliance=lambda *a, **k: stub_compliance(),
    )
    return Orchestrator(agents, drafter or Drafter(), reviewer=Reviewer())


def ready(drafter=None):
    run = stack(drafter).submit(QUESTION)
    return run.session, run.agents_fired


def gate(mcp, logger=None):
    return ApprovalGate(McpClient(mcp), settings=mcp_settings(), logger=logger)


# ---------------------------------------------------------------------------
# MCP client: allow-list only
# ---------------------------------------------------------------------------


def test_the_client_exposes_only_the_two_permitted_tools():
    client = McpClient(FakeMcp())
    assert not hasattr(client, "send_email")
    assert hasattr(client, "append_to_google_doc")
    assert hasattr(client, "create_email_draft")


def test_a_call_without_an_approval_token_is_blocked():
    mcp = FakeMcp()
    client = McpClient(mcp)
    with pytest.raises(McpError, match="MCP_NO_APPROVAL"):
        client.append_to_google_doc(None, document_id=DOC, content="x")
    with pytest.raises(McpError, match="MCP_NO_APPROVAL"):
        client.create_email_draft(None, to=MAIL, subject="s", body="b")
    assert mcp.calls == []


def test_send_email_and_any_other_tool_name_are_refused():
    mcp = FakeMcp()
    client = McpClient(mcp)
    with pytest.raises(McpError, match="MCP_TOOL_NOT_ALLOWED"):
        client.run("send_email", object(), to=MAIL, subject="s", body="b")
    with pytest.raises(McpError, match="MCP_TOOL_NOT_ALLOWED"):
        client.run("delete_doc", object())
    assert mcp.calls == []
    assert "send_email" not in MCP_TOOLS


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def test_one_approval_appends_the_doc_then_creates_an_email_draft_and_never_sends():
    mcp, sink = FakeMcp(), events()[0]
    logger = AllowListLogger(sink=sink.append)
    session, fired = ready()
    out = gate(mcp, logger).approve(session, agents_fired=fired)

    assert out.status == "done"
    assert "not sent" in out.message.lower()
    assert session.approval.status == "approved"
    assert session.approval.draft_hash == draft_hash(session.draft)
    assert [c[0] for c in mcp.calls] == ["append_to_google_doc", "create_email_draft"]
    assert mcp.calls[0][1]["document_id"] == DOC
    assert mcp.calls[1][1]["to"] == MAIL
    assert mcp.calls[1][1]["subject"] == f"HDFC Mutual Fund FAQ Assistant : {session.request_id}"
    record = mcp.calls[0][1]["content"]
    assert record == mcp.calls[1][1]["body"]
    assert session.redacted_text in record
    assert session.draft.text in record
    assert URL in record
    assert "28-Jun-2026" in record
    assert "Complaint:" in record
    assert "(redacted)" not in record
    assert "Disposition:" not in record
    assert "Team:" not in record
    assert "Signals:" not in record
    assert "Agents fired:" not in record
    logged = [e for e in sink if e.get("event") == "approval"]
    assert logged[0]["action"] == "approve"
    assert logged[0]["mcp_tools_called"] == ["append_to_google_doc", "create_email_draft"]
    assert logged[0]["draft_hash"] == session.approval.draft_hash
    assert set(logged[0]) <= {"event", "ts", "request_id", "action", "draft_hash", "mcp_tools_called"}


def test_a_second_click_is_ignored_and_does_not_write_again():
    mcp = FakeMcp()
    g, (session, fired) = gate(mcp), ready()
    first = g.approve(session, agents_fired=fired)
    second = g.approve(session, agents_fired=fired)
    assert first.status == "done" and second.status == "idempotent"
    assert [c[0] for c in mcp.calls] == ["append_to_google_doc", "create_email_draft"]


def test_a_flagged_draft_is_refused_and_nothing_is_written():
    mcp = FakeMcp()
    session, fired = ready(Scripted(ADVICE, ADVICE))
    assert session.reviewer.flagged
    out = gate(mcp).approve(session, agents_fired=fired)
    assert out.status == "refused" and out.refusal == "FLAGGED"
    assert session.approval.status == "pending"
    assert mcp.calls == []


def test_a_rejected_ticket_cannot_be_approved():
    mcp = FakeMcp()
    session, fired = ready()
    session.approval.status = "rejected"
    out = gate(mcp).approve(session, agents_fired=fired)
    assert out.status == "refused" and out.refusal == "TICKET_CLOSED"
    assert mcp.calls == []


def test_an_approval_for_an_older_draft_cannot_push_mcp():
    mcp = FakeMcp()
    g, (session, fired) = gate(mcp), ready()
    assert g.approve(session, agents_fired=fired).status == "done"
    session.draft = session.draft.model_copy(update={"text": session.draft.text + " Extra."})
    out = g.retry(session)
    assert out.status == "refused" and out.refusal == "STALE_DRAFT"
    assert [c[0] for c in mcp.calls] == ["append_to_google_doc", "create_email_draft"]


def test_a_new_approve_click_after_the_draft_changes_is_a_new_approval():
    mcp = FakeMcp()
    g, (session, fired) = gate(mcp), ready()
    g.approve(session, agents_fired=fired)
    session.draft = session.draft.model_copy(update={"text": session.draft.text + " Extra."})
    out = g.approve(session, agents_fired=fired)
    assert out.status == "done"
    assert [c[0] for c in mcp.calls] == [
        "append_to_google_doc",
        "create_email_draft",
        "append_to_google_doc",
        "create_email_draft",
    ]
    assert session.approval.draft_hash == draft_hash(session.draft)


def test_empty_mcp_settings_raise_before_any_call():
    mcp = FakeMcp()
    g = ApprovalGate(McpClient(mcp), settings=Settings())
    session, fired = ready()
    with pytest.raises(RuntimeError, match="mcp.google_doc_id"):
        g.approve(session, agents_fired=fired)
    assert mcp.calls == []
    assert session.approval.status == "pending"


def test_pii_in_the_record_blocks_the_write():
    mcp = FakeMcp()
    session, fired = ready()
    session.draft = session.draft.model_copy(update={"text": session.draft.text + f" PAN {PAN}"})
    out = gate(mcp).approve(session, agents_fired=fired)
    assert out.status == "refused" and out.refusal == "PII_IN_RECORD"
    assert mcp.calls == []
    assert session.approval.status == "pending"
    assert PAN not in (out.message or "")


def test_doc_append_failure_skips_email_keeps_approval_and_retry_does_both():
    mcp = FakeMcp()
    mcp.fail = "append_to_google_doc"
    g, (session, fired) = gate(mcp), ready()
    out = g.approve(session, agents_fired=fired)
    assert out.status == "failed"
    assert session.approval.status == "approved"
    assert session.approval.mcp_tools_called == ()
    assert [c[0] for c in mcp.calls] == ["append_to_google_doc"]
    assert "not created" in out.message.lower() or "failed" in out.message.lower()

    mcp.fail = None
    retry = g.retry(session)
    assert retry.status == "done"
    assert [c[0] for c in mcp.calls] == [
        "append_to_google_doc",
        "append_to_google_doc",
        "create_email_draft",
    ]
    assert session.approval.mcp_tools_called == ("append_to_google_doc", "create_email_draft")


def test_email_failure_after_append_retries_only_the_email():
    mcp = FakeMcp()
    mcp.fail = "create_email_draft"
    g, (session, fired) = gate(mcp), ready()
    out = g.approve(session, agents_fired=fired)
    assert out.status == "failed"
    assert session.approval.mcp_tools_called == ("append_to_google_doc",)

    mcp.fail = None
    retry = g.retry(session)
    assert retry.status == "done"
    assert [c[0] for c in mcp.calls] == [
        "append_to_google_doc",
        "create_email_draft",
        "create_email_draft",
    ]


def test_a_transport_timeout_is_a_failed_call_and_keeps_the_approval():
    class Boom(FakeMcp):
        def append_to_google_doc(self, *, document_id: str, content: str) -> None:
            self.calls.append(("append_to_google_doc", {"document_id": document_id, "content": content}))
            raise TimeoutError("slow")

    mcp = Boom()
    session, fired = ready()
    out = gate(mcp).approve(session, agents_fired=fired)
    assert out.status == "failed"
    assert session.approval.status == "approved"
    assert session.approval.mcp_tools_called == ()
    assert [c[0] for c in mcp.calls] == ["append_to_google_doc"]
    assert "send_email" not in [c[0] for c in mcp.calls]


def test_the_stored_record_has_no_raw_pii_even_when_the_complaint_had_some():
    mcp = FakeMcp()
    session, fired = ready()
    session.redacted_text = "my pan is [PAN], what is the exit load of the HDFC Large Cap fund?"
    gate(mcp).approve(session, agents_fired=fired)
    blob = mcp.calls[0][1]["content"] + mcp.calls[1][1]["body"]
    assert PAN not in blob
    assert "[PAN]" in blob


def _mcp_ids():
    from config import load_settings

    mcp = load_settings().mcp
    return mcp.google_doc_id.strip(), mcp.email_recipient.strip()


@pytest.mark.live
def test_live_mcp_writes_the_doc_and_email_draft_in_the_configured_account():
    """Skipped while [mcp] is empty. The Python process has no Cursor MCP socket."""
    doc, mail = _mcp_ids()
    if not doc or not mail:
        pytest.skip("mcp.google_doc_id and mcp.email_recipient are empty in config/settings.toml")
    pytest.skip(
        "no live MCP transport is wired into the Python process; "
        "pass an McpTransport to build_app to write to the configured account"
    )
