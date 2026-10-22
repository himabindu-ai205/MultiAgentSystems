"""Fixed vocabularies shared by every module.

Each vocabulary is declared once, as a ``Literal`` type, and the tuple of allowed
values is derived from it. Nothing else in the project should repeat these lists.
"""

from __future__ import annotations

from typing import Literal, get_args

# ---------------------------------------------------------------------------
# Corpus (Milestone 1): five HDFC scheme pages, seven sections each.
# ---------------------------------------------------------------------------

SchemeId = Literal["large_cap", "mid_cap", "small_cap", "gold_fof", "silver_fof"]
SCHEME_IDS: tuple[str, ...] = get_args(SchemeId)

SectionTitle = Literal[
    "Key fund facts",
    "Exit load",
    "Benchmark",
    "Investment objective",
    "Stamp duty and tax",
    "Fund management",
    "Minimum investments",
]
SECTION_TITLES: tuple[str, ...] = get_args(SectionTitle)

# The only legal citation links. A FactsResult may carry one of these and nothing else.
SCHEME_SOURCE_URLS: dict[str, str] = {
    "large_cap": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
    "mid_cap": "https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth",
    "small_cap": "https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth",
    "gold_fof": "https://groww.in/mutual-funds/hdfc-gold-etf-fund-of-fund-direct-plan-growth",
    "silver_fof": "https://groww.in/mutual-funds/hdfc-silver-etf-fof-direct-growth",
}
ALLOWED_CITATIONS: frozenset[str] = frozenset(SCHEME_SOURCE_URLS.values())

# ---------------------------------------------------------------------------
# Themes (Milestone 2)
# ---------------------------------------------------------------------------

ThemeId = Literal[
    "trading_charts", "money_movement", "reliability", "fees_brokerage", "other"
]
THEME_IDS: tuple[str, ...] = get_args(ThemeId)

# ---------------------------------------------------------------------------
# Agent results
# ---------------------------------------------------------------------------

FactsStatus = Literal["covered", "not_covered", "ambiguous", "error"]
PatternStatus = Literal["ok", "error"]
ComplianceStatus = Literal["ok", "error"]
PatternSignal = Literal["one_off", "recurring", "rising"]
PatternBasis = Literal["keyword_match", "theme_level"]
ComplianceMode = Literal["inbound", "outbound"]
FlagType = Literal[
    "advice_request", "advice_in_text", "performance_claim", "pii_residual"
]

PiiType = Literal["PAN", "AADHAAR", "PHONE", "EMAIL", "OTP", "ACCOUNT_NO"]
PII_TYPES: tuple[str, ...] = get_args(PiiType)

# ---------------------------------------------------------------------------
# Orchestrator, reviewer, refinement, approval
# ---------------------------------------------------------------------------

DispositionValue = Literal[
    "SEND_READY", "HOLD_DISAGREEMENT", "NOT_COVERED", "REFUSAL", "HOLD_AGENT_FAILURE"
]
DISPOSITION_VALUES: tuple[str, ...] = get_args(DispositionValue)

DraftKind = Literal["answer", "not_covered", "refusal", "escalation_hold"]
# "shorter" is what the human asks for in a refinement (Phase 8). It re-drafts only.
ReplyStyle = Literal["standard", "shorter"]
REPLY_STYLES: tuple[str, ...] = get_args(ReplyStyle)
Priority = Literal["P1", "P2", "P3"]

ReviewVerdict = Literal["PASS", "FAIL"]
FailureCode = Literal[
    "UNCITED_CLAIM",
    "CITATION_MISMATCH",
    "ADVICE_LANGUAGE",
    "PII_SURVIVED",
    "TOO_LONG",
    "MISSING_LAST_CHECKED",
    # Two codes the Reviewer needs to fail closed. A blank draft has no claim to check, and
    # a failed outbound Compliance call must never read as a pass (Edge case REV-17).
    "EMPTY_DRAFT",
    "COMPLIANCE_CHECK_FAILED",
]
FAILURE_CODES: tuple[str, ...] = get_args(FailureCode)

# Agents that can be started, or reused from cache, by the Orchestrator.
FiredAgent = Literal["facts", "pattern", "compliance", "orchestrator"]
FIRED_AGENTS: tuple[str, ...] = get_args(FiredAgent)

RefinementCategory = Literal[
    "style", "scheme_change", "pattern_check", "compliance_concern", "unclear"
]
REFINEMENT_CATEGORIES: tuple[str, ...] = get_args(RefinementCategory)

# The only labels the feedback classifier may return. "shorter" is the one style change the
# drafter supports, so it is a label of its own and not the whole "style" category.
FeedbackLabel = Literal["shorter", "scheme_change", "pattern_check", "compliance_concern", "unclear"]
FEEDBACK_LABELS: tuple[str, ...] = get_args(FeedbackLabel)

ApprovalStatus = Literal["pending", "approved", "rejected"]
ApprovalAction = Literal["approve", "reject", "retry"]

# The only MCP tools the approval gate may ever call. `send_email` is deliberately absent.
MCP_TOOLS: tuple[str, ...] = ("append_to_google_doc", "create_email_draft")

# ---------------------------------------------------------------------------
# Caps fixed by the problem statement
# ---------------------------------------------------------------------------

MAX_REVIEWER_REWORK_ROUNDS = 1
MAX_REFINEMENT_ROUNDS = 5
MAX_REPLY_SENTENCES = 3
