"""A stdio MCP stand-in for tests. Speaks Content-Length JSON-RPC. Never talks to Google."""

from __future__ import annotations

import json
import sys


def _read() -> dict:
    line = sys.stdin.buffer.readline()
    if not line:
        raise SystemExit(0)
    return json.loads(line.decode("utf-8"))


def _write(payload: dict) -> None:
    sys.stdout.buffer.write((json.dumps(payload) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def _envelope(message: dict) -> dict:
    params = message.get("params") or {}
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if name == "append_to_google_doc":
        if not arguments.get("documentId") or not arguments.get("content"):
            return {"success": False, "error": {"code": "VALIDATION_ERROR"}}
        return {"success": True, "documentId": arguments["documentId"]}
    if name == "create_email_draft":
        to = arguments.get("to") or []
        if not to or not arguments.get("subject") or not arguments.get("body"):
            return {"success": False, "error": {"code": "VALIDATION_ERROR"}}
        return {"success": True, "draftId": "draft-test-1"}
    if name == "send_email":
        return {"success": False, "error": {"code": "UNKNOWN_TOOL"}}
    return {"success": False, "error": {"code": "UNKNOWN_TOOL"}}


def main() -> None:
    while True:
        message = _read()
        method = message.get("method")
        if method == "initialize":
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "gmail-fake", "version": "0"},
                    },
                }
            )
            continue
        if method == "notifications/initialized":
            continue
        if method == "tools/call":
            envelope = _envelope(message)
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(envelope)}],
                        "isError": envelope.get("success") is not True,
                    },
                }
            )
            continue
        if "id" in message:
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {"code": -32601, "message": "method not found"},
                }
            )


if __name__ == "__main__":
    main()
