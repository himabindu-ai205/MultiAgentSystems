"""The two permitted MCP tools, and nothing else (Architecture section 12).

The Gmail MCP server also exposes ``send_email``. That method is deliberately absent
here, so auto-send is impossible by construction. Every call needs an ``ApprovalToken``
minted by the gate; a missing or forged token never reaches the transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from constants import MCP_TOOLS


class McpError(Exception):
    """An MCP call was blocked or failed. The message is a fixed code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ApprovalToken:
    """Proof that the human clicked approve for this draft. Minted only by the gate."""

    request_id: str
    draft_hash: str


class McpTransport(Protocol):
    def append_to_google_doc(self, *, document_id: str, content: str) -> None: ...
    def create_email_draft(self, *, to: str, subject: str, body: str) -> str: ...


class UnconfiguredTransport:
    """Used when no real MCP backend is wired. Calls fail closed, they never send."""

    def append_to_google_doc(self, *, document_id: str, content: str) -> None:
        raise McpError("MCP_NOT_CONFIGURED")

    def create_email_draft(self, *, to: str, subject: str, body: str) -> str:
        raise McpError("MCP_NOT_CONFIGURED")


class McpClient:
    """Allow-listed wrapper. ``send_email`` is not a method."""

    def __init__(self, transport: McpTransport | None = None) -> None:
        self._transport: McpTransport = transport or UnconfiguredTransport()

    def append_to_google_doc(
        self, token: ApprovalToken | None, *, document_id: str, content: str
    ) -> None:
        self.run("append_to_google_doc", token, document_id=document_id, content=content)

    def create_email_draft(
        self, token: ApprovalToken | None, *, to: str, subject: str, body: str
    ) -> str:
        result = self.run("create_email_draft", token, to=to, subject=subject, body=body)
        return result if isinstance(result, str) else ""

    def run(self, tool: str, token: ApprovalToken | None, **kwargs: Any) -> Any:
        """Call one permitted tool. Anything else, including ``send_email``, is refused."""
        if tool not in MCP_TOOLS:
            raise McpError("MCP_TOOL_NOT_ALLOWED")
        if not isinstance(token, ApprovalToken):
            raise McpError("MCP_NO_APPROVAL")
        method = getattr(self._transport, tool, None)
        if not callable(method):
            raise McpError("MCP_NOT_CONFIGURED")
        try:
            return method(**kwargs)
        except McpError:
            raise
        except Exception:
            raise McpError("MCP_CALL_FAILED") from None


__all__ = [
    "ApprovalToken",
    "McpClient",
    "McpError",
    "McpTransport",
    "UnconfiguredTransport",
]
