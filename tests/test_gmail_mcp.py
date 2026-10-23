"""The desk talks to the Gmail MCP server over stdio, and never asks it to send."""

from __future__ import annotations

import sys
from pathlib import Path

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from approval.gmail_mcp import GmailMcpTransport, HttpGmailMcpTransport, build_gmail_mcp_transport
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
        Settings(mcp={"server_dir": str(Path("C:/missing-mcp-server"))}),
        environ={"PATH": ""},
    )
    assert isinstance(transport, UnconfiguredTransport)


def test_a_hosted_url_uses_http_and_never_asks_to_send():
    calls: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            calls.append(body)
            assert self.headers.get("Authorization") == "Bearer secret"
            name = body["name"]
            assert name != "send_email"
            payload = {"success": True, "draftId": "draft-http-1"} if name == "create_email_draft" else {"success": True}
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        transport = build_gmail_mcp_transport(
            Settings(mcp={"server_url": url}),
            environ={"MCP_HTTP_TOKEN": "secret", "PATH": ""},
        )
        assert isinstance(transport, HttpGmailMcpTransport)
        client = McpClient(transport)
        client.append_to_google_doc(TOKEN, document_id="doc-1", content="record")
        assert client.create_email_draft(TOKEN, to="a@b.com", subject="s", body="b") == "draft-http-1"
        assert [c["name"] for c in calls] == ["append_to_google_doc", "create_email_draft"]
    finally:
        server.shutdown()
        server.server_close()
