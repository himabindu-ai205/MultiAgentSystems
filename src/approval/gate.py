"""The approval gate (Implementation Plan Phase 9, Architecture section 12).

Only an explicit approve click creates a record. The record is bound to ``draft_hash``.
MCP runs only after that, document first then email draft, and never a send. A failed
MCP call keeps the approval so the human can retry the missing step.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from approval.mcp_client import ApprovalToken, McpClient, McpError
from config import Settings
from constants import FIRED_AGENTS, MCP_TOOLS
from ingress.redactor import find_pii
from logging_allowlist import AllowListLogger
from schemas import ApprovalState, ReplyDraft, Session

DONE = frozenset(MCP_TOOLS)

SUCCESS = (
    "The approved reply was appended to the support Google Doc. "
    "A Gmail draft was created. The email was not sent."
)
IDEMPOTENT = "Already approved. Nothing more was written."
APPEND_FAIL = (
    "Approval is kept. The Google Doc append failed, so the Gmail draft was not created. "
    "Nothing was sent."
)
EMAIL_FAIL = (
    "Approval is kept. The Google Doc was updated. The Gmail draft failed. Nothing was sent."
)
MESSAGES = {
    "FLAGGED": "Approve isn't available for this draft.",
    "TICKET_CLOSED": "This ticket is closed, so it cannot be approved.",
    "NO_DRAFT": "There is no draft to approve.",
    "NOT_PASSED": "Only a draft the Reviewer passed can be approved.",
    "STALE_DRAFT": "The draft changed after approval. Approve the current draft again.",
    "PII_IN_RECORD": "The record still has personal details, so nothing was written.",
    "NOT_APPROVED": "There is no approval to retry.",
}


@dataclass(frozen=True)
class ApprovalOutcome:
    status: Literal["done", "refused", "failed", "idempotent"]
    message: str
    refusal: str | None = None
    tools_called: tuple[str, ...] = ()


def draft_hash(draft: ReplyDraft | None) -> str:
    """SHA-256 of the draft the human saw. Changing a word voids the approval."""
    if draft is None:
        raise ValueError("no draft")
    payload = json.dumps(
        {
            "text": draft.text,
            "citation": draft.citation,
            "last_checked": draft.last_checked,
            "kind": draft.kind,
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def mcp_complete(session: Session) -> bool:
    return set(session.approval.mcp_tools_called) >= DONE


def void_approval(session: Session) -> None:
    """The draft changed, or the human discarded. Nothing is stored."""
    session.approval = ApprovalState()


def email_subject(request_id: str) -> str:
    return f"HDFC Mutual Fund FAQ Assistant : {request_id}"


def build_record(session: Session, agents_fired: Sequence[str]) -> str:
    """The text written to the doc and the email draft. FAQ fields only."""
    del agents_fired
    draft = session.draft
    return "\n".join(
        [
            f"Ticket: {session.request_id}",
            f"Complaint: {session.redacted_text}",
            f"Reply: {draft.text if draft else '(none)'}",
            f"Citation: {draft.citation if draft else '(none)'}",
            f"Last checked: {draft.last_checked if draft else '(none)'}",
        ]
    )


class ApprovalGate:
    def __init__(
        self,
        client: McpClient,
        *,
        settings: Settings,
        logger: AllowListLogger | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._logger = logger

    def approve(self, session: Session, *, agents_fired: Sequence[str]) -> ApprovalOutcome:
        """Create the approval from a click, then write the record through MCP."""
        refused = self._eligibility(session)
        if refused:
            return refused
        current = draft_hash(session.draft)
        if session.approval.status == "approved" and session.approval.draft_hash == current:
            if mcp_complete(session):
                return ApprovalOutcome(status="idempotent", message=IDEMPOTENT, tools_called=session.approval.mcp_tools_called)
            return self._write(session, action="retry")
        if session.approval.status == "approved" and session.approval.draft_hash != current:
            void_approval(session)
        return self._commit(session, current, agents_fired)

    def retry(self, session: Session) -> ApprovalOutcome:
        """Finish a recorded approval whose MCP write did not complete."""
        if session.approval.status == "rejected":
            return self._refuse("TICKET_CLOSED")
        if session.approval.status != "approved" or not session.approval.draft_hash:
            return self._refuse("NOT_APPROVED")
        if session.draft is None or draft_hash(session.draft) != session.approval.draft_hash:
            return self._refuse("STALE_DRAFT")
        if mcp_complete(session):
            return ApprovalOutcome(status="idempotent", message=IDEMPOTENT, tools_called=session.approval.mcp_tools_called)
        return self._write(session, action="retry")

    # -- internals ---------------------------------------------------------

    def _eligibility(self, session: Session) -> ApprovalOutcome | None:
        if session.approval.status == "rejected":
            return self._refuse("TICKET_CLOSED")
        if session.reviewer.flagged:
            return self._refuse("FLAGGED")
        if session.draft is None:
            return self._refuse("NO_DRAFT")
        if session.reviewer.last_verdict != "PASS":
            return self._refuse("NOT_PASSED")
        return None

    def _commit(
        self, session: Session, current: str, agents_fired: Sequence[str]
    ) -> ApprovalOutcome:
        self._settings.require("mcp.google_doc_id", "mcp.email_recipient")
        fired = tuple(a for a in agents_fired if a in FIRED_AGENTS)
        record = build_record(session, fired)
        if find_pii(record):
            return self._refuse("PII_IN_RECORD")
        session.approval.status = "approved"
        session.approval.draft_hash = current
        session.approval.mcp_tools_called = ()
        session.approval.agents_fired = fired  # type: ignore[assignment]
        return self._write(session, action="approve")

    def _write(self, session: Session, *, action: str) -> ApprovalOutcome:
        self._settings.require("mcp.google_doc_id", "mcp.email_recipient")
        record = build_record(session, session.approval.agents_fired)
        if find_pii(record):
            return self._refuse("PII_IN_RECORD")
        token = ApprovalToken(request_id=session.request_id, draft_hash=session.approval.draft_hash or "")
        done = list(session.approval.mcp_tools_called)

        if "append_to_google_doc" not in done:
            try:
                self._client.append_to_google_doc(
                    token,
                    document_id=self._settings.mcp.google_doc_id,
                    content=record,
                )
            except McpError:
                self._log(session, action, done)
                return ApprovalOutcome(status="failed", message=APPEND_FAIL, tools_called=tuple(done))
            done.append("append_to_google_doc")
            session.approval.mcp_tools_called = tuple(done)

        if "create_email_draft" not in done:
            try:
                self._client.create_email_draft(
                    token,
                    to=self._settings.mcp.email_recipient,
                    subject=email_subject(session.request_id),
                    body=record,
                )
            except McpError:
                self._log(session, action, done)
                return ApprovalOutcome(status="failed", message=EMAIL_FAIL, tools_called=tuple(done))
            done.append("create_email_draft")
            session.approval.mcp_tools_called = tuple(done)

        self._log(session, action, done)
        return ApprovalOutcome(status="done", message=SUCCESS, tools_called=tuple(done))

    def _refuse(self, code: str) -> ApprovalOutcome:
        return ApprovalOutcome(status="refused", message=MESSAGES[code], refusal=code)

    def _log(self, session: Session, action: str, tools: Sequence[str]) -> None:
        if self._logger is None or not session.approval.draft_hash:
            return
        self._logger.log(
            "approval",
            request_id=session.request_id,
            action=action,
            draft_hash=session.approval.draft_hash,
            mcp_tools_called=list(tools),
        )


__all__ = [
    "ApprovalGate",
    "ApprovalOutcome",
    "build_record",
    "email_subject",
    "draft_hash",
    "mcp_complete",
    "void_approval",
]
