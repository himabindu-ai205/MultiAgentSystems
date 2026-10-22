"""Typed contracts for every component (Architecture sections 7 and 11.1).

Rules built into the types:

* Results are immutable and reject unknown fields, so a malformed agent output
  fails loudly instead of being passed on.
* ``FactsResult.citation`` can only be one of the five corpus ``source_url`` values.
* ``Session`` has no field for the raw complaint. Unknown fields are rejected, both
  when the object is built and when a value is assigned later, so raw text cannot be
  stored under any name other than ``redacted_text``.
* ``Session`` prints only its redacted text, IDs, statuses and counters.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from constants import (
    ALLOWED_CITATIONS,
    ApprovalStatus,
    ComplianceMode,
    ComplianceStatus,
    DispositionValue,
    DraftKind,
    FactsStatus,
    FailureCode,
    FiredAgent,
    FlagType,
    MAX_REFINEMENT_ROUNDS,
    MAX_REVIEWER_REWORK_ROUNDS,
    MCP_TOOLS,
    PatternBasis,
    PatternSignal,
    PatternStatus,
    Priority,
    RefinementCategory,
    ReplyStyle,
    ReviewVerdict,
    SchemeId,
    SectionTitle,
    ThemeId,
)

# ---------------------------------------------------------------------------
# Small constrained types
# ---------------------------------------------------------------------------

# Review IDs are 16 hex characters. Allowing only that shape keeps review text out
# of ``matched_review_ids``.
ReviewId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")]

# A short rule or category code, for example "rule.buy_sell" or "classifier.advice".
# No spaces are allowed, so a matched piece of user text cannot be placed in it.
RuleCode = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_.:-]{1,64}$")]

SignalValue = Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,32}$")]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(ge=0)]


def _check_uuid(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("request_id must be a UUID") from exc
    if str(parsed) != value:
        raise ValueError("request_id must be a lower-case canonical UUID")
    return value


RequestId = Annotated[str, Field(min_length=36, max_length=36)]


class _Frozen(BaseModel):
    """Immutable, strict value object."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class _Strict(BaseModel):
    """Mutable object that still rejects unknown fields on construction and assignment."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ---------------------------------------------------------------------------
# Facts Agent
# ---------------------------------------------------------------------------


class Searched(_Frozen):
    schemes: tuple[SchemeId, ...] = ()
    sections: tuple[SectionTitle, ...] = ()


class FactsResult(_Frozen):
    agent: Annotated[str, StringConstraints(pattern=r"^facts$")] = "facts"
    status: FactsStatus
    answer: str | None = None
    citation: str | None = None
    scheme_id: SchemeId | None = None
    section_title: SectionTitle | None = None
    searched: Searched = Field(default_factory=Searched)
    source_fetched_at: str | None = None
    candidates: tuple[SchemeId, ...] = ()
    error: str | None = None

    @field_validator("citation")
    @classmethod
    def _citation_is_a_corpus_url(cls, value: str | None) -> str | None:
        if value is not None and value not in ALLOWED_CITATIONS:
            raise ValueError("citation must be one of the five corpus source_url values")
        return value

    @model_validator(mode="after")
    def _status_rules(self) -> FactsResult:
        if self.status == "covered":
            missing = [
                name
                for name in ("answer", "citation", "scheme_id", "section_title", "source_fetched_at")
                if getattr(self, name) in (None, "")
            ]
            if missing:
                raise ValueError("covered result is missing: " + ", ".join(missing))
            if self.error is not None:
                raise ValueError("covered result must not carry an error")
        elif self.status == "not_covered":
            if self.citation is not None or self.answer is not None:
                raise ValueError("not_covered result must have no answer and no citation")
            if not (self.searched.schemes or self.searched.sections):
                raise ValueError("not_covered result must say what was searched")
        elif self.status == "ambiguous":
            if not self.candidates:
                raise ValueError("ambiguous result must list candidate schemes")
            if self.citation is not None or self.answer is not None:
                raise ValueError("ambiguous result must have no answer and no citation")
        else:  # error
            if not self.error:
                raise ValueError("error result must carry an error code")
            if self.citation is not None or self.answer is not None:
                raise ValueError("error result must have no answer and no citation")
        return self


# ---------------------------------------------------------------------------
# Pattern Agent
# ---------------------------------------------------------------------------


class Window(_Frozen):
    start: date
    end: date
    days: int = Field(ge=1)
    anchor_date: date

    @model_validator(mode="after")
    def _order(self) -> Window:
        if self.end < self.start:
            raise ValueError("window end is before its start")
        return self


class PatternResult(_Frozen):
    agent: Annotated[str, StringConstraints(pattern=r"^pattern$")] = "pattern"
    status: PatternStatus
    theme_id: ThemeId | None = None
    signal: PatternSignal | None = None
    count_current: Count = 0
    count_prior: Count = 0
    window: Window | None = None
    basis: PatternBasis | None = None
    matched_review_ids: tuple[ReviewId, ...] = ()
    error: str | None = None

    @model_validator(mode="after")
    def _status_rules(self) -> PatternResult:
        if self.status == "ok":
            missing = [
                name
                for name in ("theme_id", "signal", "window", "basis")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError("ok result is missing: " + ", ".join(missing))
            if self.error is not None:
                raise ValueError("ok result must not carry an error")
        elif not self.error:
            raise ValueError("error result must carry an error code")
        return self


# ---------------------------------------------------------------------------
# Compliance Agent
# ---------------------------------------------------------------------------


class Flag(_Frozen):
    type: FlagType
    # Which rule or category fired. Never the matched text.
    detail: RuleCode


class PiiCounts(_Frozen):
    """How many items of each PII type were found. Counts only, never values."""

    PAN: Count = 0
    AADHAAR: Count = 0
    PHONE: Count = 0
    EMAIL: Count = 0
    OTP: Count = 0
    ACCOUNT_NO: Count = 0


class ComplianceResult(_Frozen):
    agent: Annotated[str, StringConstraints(pattern=r"^compliance$")] = "compliance"
    mode: ComplianceMode
    status: ComplianceStatus
    flags: tuple[Flag, ...] = ()
    pii_redacted: PiiCounts = Field(default_factory=PiiCounts)
    clean: bool
    error: str | None = None

    @model_validator(mode="after")
    def _status_rules(self) -> ComplianceResult:
        if self.status == "ok":
            if self.error is not None:
                raise ValueError("ok result must not carry an error")
            if self.clean != (len(self.flags) == 0):
                raise ValueError("clean must be true exactly when there are no flags")
        else:
            if not self.error:
                raise ValueError("error result must carry an error code")
            if self.clean:
                raise ValueError("an error result can never be clean")
        return self


# ---------------------------------------------------------------------------
# Orchestrator outputs
# ---------------------------------------------------------------------------


class ReplyDraft(_Frozen):
    """The draft is deliberately not checked here for length or citation form.

    Those are the Reviewer's checks, so a bad draft must be representable.
    """

    text: str = Field(min_length=1)
    citation: str | None = None
    last_checked: str | None = None
    kind: DraftKind


class Signals(_Frozen):
    facts: SignalValue
    pattern: SignalValue
    compliance: SignalValue


class RoutingNote(_Frozen):
    team: str = Field(min_length=1)
    priority: Priority
    reason: str = Field(min_length=1)
    signals: Signals
    disagreement: str | None = None


class Disposition(_Frozen):
    value: DispositionValue


class ReviewerResult(_Frozen):
    verdict: ReviewVerdict
    round: int = Field(ge=1, le=MAX_REVIEWER_REWORK_ROUNDS + 1)
    failure_types: tuple[FailureCode, ...] = ()
    # A short instruction for the rewrite. It names the failure and never quotes the draft.
    instruction: str | None = None
    flagged: bool = False

    @model_validator(mode="after")
    def _rules(self) -> ReviewerResult:
        if self.verdict == "PASS" and (self.failure_types or self.flagged):
            raise ValueError("a PASS cannot carry failures or a flag")
        if self.verdict == "FAIL" and not self.failure_types:
            raise ValueError("a FAIL must name at least one failure")
        if self.flagged and self.round <= MAX_REVIEWER_REWORK_ROUNDS:
            raise ValueError("a draft is only flagged after the rework round has failed")
        return self


class ReviewHandoff(_Frozen):
    """What the Orchestrator gives the Reviewer: the draft, the note and the evidence.

    It holds no complaint text. ``round`` is 1 for a first look at a draft and 2 for the
    look after the one allowed rework.
    """

    request_id: RequestId
    draft: ReplyDraft
    routing_note: RoutingNote
    facts: FactsResult | None = None
    round: int = Field(1, ge=1, le=MAX_REVIEWER_REWORK_ROUNDS + 1)

    @field_validator("request_id")
    @classmethod
    def _request_id_is_a_uuid(cls, value: str) -> str:
        return _check_uuid(value)


# ---------------------------------------------------------------------------
# Session state (Architecture section 11.1)
# ---------------------------------------------------------------------------


class SessionResults(_Strict):
    facts: FactsResult | None = None
    pattern: PatternResult | None = None
    compliance_inbound: ComplianceResult | None = None


class ReviewerState(_Strict):
    rounds_used: int = Field(0, ge=0, le=MAX_REVIEWER_REWORK_ROUNDS + 1)
    last_verdict: ReviewVerdict | None = None
    flagged: bool = False


class RefinementEntry(_Frozen):
    category: RefinementCategory
    agents_fired: tuple[FiredAgent, ...]
    agents_cached: tuple[FiredAgent, ...] = ()


class RefinementState(_Strict):
    rounds_used: int = Field(0, ge=0, le=MAX_REFINEMENT_ROUNDS)
    history: tuple[RefinementEntry, ...] = ()
    # What the human has asked for so far, as fixed values and never as their words. They
    # are kept so a later refinement does not undo an earlier one ("shorter", then a
    # different scheme, stays shorter).
    style: ReplyStyle = "standard"
    scheme_override: SchemeId | None = None
    theme_hint: ThemeId | None = None
    window_shift_weeks: int = Field(0, ge=0, le=52)


class ApprovalState(_Strict):
    status: ApprovalStatus = "pending"
    draft_hash: Sha256Hex | None = None
    # Tools that already succeeded for this hash, so a retry does not repeat them.
    mcp_tools_called: tuple[str, ...] = ()
    # Snapshot at the approve click, for the stored record.
    agents_fired: tuple[FiredAgent, ...] = ()

    @field_validator("mcp_tools_called")
    @classmethod
    def _only_permitted_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        bad = [name for name in value if name not in MCP_TOOLS]
        if bad:
            raise ValueError("mcp_tools_called can only hold the two permitted MCP tools")
        return value


class Session(_Strict):
    """State for one ticket. It lives in memory and is never saved before approval.

    There is no field for the raw complaint. Ingress redaction produces
    ``redacted_text``, and that is the only complaint text this object can hold.
    """

    request_id: RequestId
    redacted_text: str
    pii_manifest: PiiCounts = Field(default_factory=PiiCounts)
    results: SessionResults = Field(default_factory=SessionResults)
    draft: ReplyDraft | None = None
    routing_note: RoutingNote | None = None
    disposition: DispositionValue | None = None
    reviewer: ReviewerState = Field(default_factory=ReviewerState)
    refinement: RefinementState = Field(default_factory=RefinementState)
    advice_attempts: Count = 0
    approval: ApprovalState = Field(default_factory=ApprovalState)

    @field_validator("request_id")
    @classmethod
    def _request_id_is_a_uuid(cls, value: str) -> str:
        return _check_uuid(value)

    # Printing a session shows the redacted text, IDs, statuses and counters only.
    def __repr_args__(self) -> list[tuple[str, Any]]:  # type: ignore[override]
        review_ids = self.results.pattern.matched_review_ids if self.results.pattern else ()
        return [
            ("request_id", self.request_id),
            ("redacted_text", self.redacted_text),
            ("disposition", self.disposition),
            ("facts", self.results.facts.status if self.results.facts else None),
            ("pattern", self.results.pattern.signal if self.results.pattern else None),
            (
                "compliance",
                self.results.compliance_inbound.status if self.results.compliance_inbound else None,
            ),
            ("review_ids", list(review_ids)),
            ("reviewer_rounds", self.reviewer.rounds_used),
            ("refinement_rounds", self.refinement.rounds_used),
            ("approval", self.approval.status),
        ]


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def new_request_id() -> str:
    """A fresh lower-case canonical UUID for a ticket."""
    return str(uuid.uuid4())


def is_request_id(value: Any) -> bool:
    """True when ``value`` is a lower-case canonical UUID string."""
    return isinstance(value, str) and bool(_UUID_RE.match(value))
