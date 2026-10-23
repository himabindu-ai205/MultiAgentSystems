"""Stdio client for the project's Gmail MCP server (Architecture section 12).

The desk process is not Cursor, so it cannot use the IDE's MCP socket. It spawns the
same ``node dist/index.js`` command Cursor uses and calls only the two allowed tools.
``send_email`` is never requested.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from approval.mcp_client import McpError, McpTransport, UnconfiguredTransport
from config import Settings
from paths import PROJECT_ROOT

PROTOCOL = "2024-11-05"
DEFAULT_SERVER_DIR = PROJECT_ROOT.parent / "MCP Server"


class GmailMcpTransport:
    """JSON-RPC stdio client for ``append_to_google_doc`` and ``create_email_draft``."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._command = command
        self._cwd = cwd
        self._env = dict(env) if env is not None else dict(os.environ)
        self._timeout = timeout_seconds
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._next_id = 1
        self._ready = False

    def append_to_google_doc(self, *, document_id: str, content: str) -> None:
        envelope = self._call("append_to_google_doc", {"documentId": document_id, "content": content})
        self._require_ok(envelope)

    def create_email_draft(self, *, to: str, subject: str, body: str) -> str:
        envelope = self._call(
            "create_email_draft",
            {"to": [to], "subject": subject, "body": body},
        )
        self._require_ok(envelope)
        draft_id = envelope.get("draftId")
        return str(draft_id) if draft_id else ""

    def close(self) -> None:
        with self._lock:
            self._stop()

    def _require_ok(self, envelope: dict[str, Any]) -> None:
        if envelope.get("success") is True:
            return
        error = envelope.get("error") if isinstance(envelope.get("error"), dict) else {}
        code = str(error.get("code") or "MCP_CALL_FAILED")
        raise McpError(code)

    def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._ensure()
            result = self._rpc(
                "tools/call",
                {"name": name, "arguments": arguments},
            )
        if not isinstance(result, dict):
            raise McpError("MCP_CALL_FAILED")
        text = ""
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text = str(block.get("text") or "")
                break
        if not text:
            raise McpError("MCP_CALL_FAILED")
        try:
            envelope = json.loads(text)
        except json.JSONDecodeError as exc:
            raise McpError("MCP_CALL_FAILED") from exc
        if not isinstance(envelope, dict):
            raise McpError("MCP_CALL_FAILED")
        return envelope

    def _ensure(self) -> None:
        if self._ready and self._proc is not None and self._proc.poll() is None:
            return
        self._stop()
        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(
            self._command,
            cwd=str(self._cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=self._env,
            creationflags=flags,
        )
        self._next_id = 1
        self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "groww-desk", "version": "1.0.0"},
            },
        )
        self._notify("notifications/initialized")
        self._ready = True

    def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        request_id = self._next_id
        self._next_id += 1
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            message = self._read()
            if message.get("id") == request_id:
                if "error" in message:
                    raise McpError("MCP_CALL_FAILED")
                return message.get("result")

    def _notify(self, method: str) -> None:
        self._write({"jsonrpc": "2.0", "method": method})

    def _write(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise McpError("MCP_NOT_CONFIGURED")
        raw = (json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8")
        proc.stdin.write(raw)
        proc.stdin.flush()

    def _read(self) -> dict[str, Any]:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise McpError("MCP_NOT_CONFIGURED")
        line = proc.stdout.readline()
        if not line:
            raise McpError("MCP_CALL_FAILED")
        try:
            parsed = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise McpError("MCP_CALL_FAILED") from exc
        if not isinstance(parsed, dict):
            raise McpError("MCP_CALL_FAILED")
        return parsed

    def _stop(self) -> None:
        self._ready = False
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.stdin:
            try:
                proc.stdin.close()
            except OSError:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def resolve_server_dir(settings: Settings, environ: Mapping[str, str] | None = None) -> Path:
    env = environ if environ is not None else os.environ
    chosen = (settings.mcp.server_dir or env.get("MCP_SERVER_DIR") or "").strip()
    return Path(chosen) if chosen else DEFAULT_SERVER_DIR


def build_gmail_mcp_transport(
    settings: Settings,
    *,
    environ: Mapping[str, str] | None = None,
) -> McpTransport:
    """The real Gmail MCP process when ``dist/index.js`` is present, otherwise a no-op."""
    env = dict(os.environ if environ is None else environ)
    root = resolve_server_dir(settings, env)
    script = root / "dist" / "index.js"
    node = shutil.which("node", path=env.get("PATH"))
    if node is None or not script.is_file():
        return UnconfiguredTransport()
    return GmailMcpTransport([node, str(script)], cwd=root, env=env)


__all__ = [
    "GmailMcpTransport",
    "build_gmail_mcp_transport",
    "resolve_server_dir",
]
