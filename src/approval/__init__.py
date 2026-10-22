"""Approval gate and MCP client (Implementation Plan Phase 9, Architecture section 12)."""

from approval.gate import ApprovalGate, draft_hash
from approval.mcp_client import McpClient, McpError

__all__ = ["ApprovalGate", "McpClient", "McpError", "draft_hash"]
