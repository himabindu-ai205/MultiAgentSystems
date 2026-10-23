"""The desk talks to the Gmail MCP server over stdio, and never asks it to send."""

from __future__ import annotations

import sys
from pathlib import Path

from approval.gmail_mcp import GmailMcpTransport, build_gmail_mcp_transport
from approval.mcp_client import ApprovalToken, McpClient, UnconfiguredTransport
from config import Settings
from paths import PROJECT_ROOT

FAKE = PROJECT_ROOT / "tests" / "fake_gmail_mcp.py"
TOKEN = ApprovalToken(request_id="req-1", draft_hash="abc")


def test_the_stdio_client_appends_and_drafts_and_never_sends():
    transport = GmailMcpTransport([sys.executable, str(FAKE)], cwd=FAKE.parent)
    try:
        client = McpClient(transport)
        client.append_to_google_doc(TOKEN, document_id="doc-1", content="record")
        draft_id = client.create_email_draft(
            TOKEN, to="desk@example.com", subject="Support record req-1", body="record"
        )
        assert draft_id == "draft-test-1"
        assert not hasattr(client, "send_email")
    finally:
        transport.close()


def test_a_missing_server_binary_stays_unconfigured():
    transport = build_gmail_mcp_transport(
        Settings(mcp={"server_dir": str(Path("C:/missing-mcp-server"))})
    )
    assert isinstance(transport, UnconfiguredTransport)
