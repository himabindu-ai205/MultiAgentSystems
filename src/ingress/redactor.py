"""Ingress redaction (Architecture section 5).

A deterministic function, not an agent and not a model. The Orchestrator calls it
first, and every later component sees only its output.

Rules this module keeps:

* Each PII match becomes a typed placeholder such as ``[PAN]``.
* The manifest holds counts per type. It never holds a value or a position.
* Where a pattern is ambiguous, it redacts. A false positive is cheap, a leak is not.
* Nothing here logs, prints or puts raw text into an exception or a ``repr``.
* Redaction is idempotent: redacting already redacted text changes nothing.

The detectors are also exported (``find_pii`` and ``contains_pii``), so the Compliance
Agent and the Reviewer reuse the same rules for their PII checks.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from constants import PII_TYPES
from schemas import is_request_id, new_request_id

DEFAULT_MAX_CHARS = 5000
_MAX_PASSES = 3

PLACEHOLDERS: Mapping[str, str] = MappingProxyType({t: f"[{t}]" for t in PII_TYPES})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

_ERROR_TEXT = {
    "EMPTY_INPUT": "The complaint is empty.",
    "NOT_TEXT": "The complaint must be text.",
    "NO_USABLE_TEXT": "The complaint has no usable text.",
    "TOO_LONG": "The complaint is longer than the allowed limit.",
    "BAD_REQUEST_ID": "The request ID is not a valid UUID.",
    "REDACTION_FAILED": "Redaction failed, so nothing was processed.",
}


class IngressError(Exception):
    """Ingress could not produce a redacted complaint.

    The message is fixed text chosen by ``code``. It never includes the input, so it
    is safe to log and to show.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(_ERROR_TEXT[code])


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

_FLAGS = re.IGNORECASE | re.ASCII

# Words that make a nearby digit string an OTP or an account number.
_OTP_WORD = r"(?:otp|one[ -]?time[ -]?(?:password|code|pin)|verification[ -]code|passcode|code|pin)"
_ACCOUNT_WORD = r"(?:a/c|acct|account|acc)"
_AADHAAR_WORD = r"(?:aadhaar|aadhar|adhaar|adhar|uidai|uid)"

_NUM_OTP = r"(?<!\d)(?P<num>\d{4,8})(?!\d)"
_NUM_ACCOUNT = r"(?<!\d)(?P<num>\d(?:[ -]?\d){8,17})(?!\d)"
_NUM_AADHAAR = r"(?<!\d)(?P<num>\d{4}[ -]?\d{4}[ -]?\d{4})(?!\d)"

_BEFORE = r"(?<![A-Za-z])"
_AFTER = r"(?![A-Za-z])"
_GAP_FWD = r"[^\d\n]{0,30}?"
_GAP_BACK = r"[^\d\n]{0,20}?"

# Order is priority. A later detector never claims text an earlier one already took.
_DETECTORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "EMAIL",
        re.compile(
            r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+",
            _FLAGS,
        ),
    ),
    (
        # "name at gmail dot com". Needs both words, to stay narrow.
        "EMAIL",
        re.compile(
            r"[A-Za-z0-9._\-]+\s*(?:\(at\)|\[at\]|\bat\b)\s*[A-Za-z0-9\-]+"
            r"\s*(?:\(dot\)|\[dot\]|\bdot\b)\s*[A-Za-z]{2,}",
            _FLAGS,
        ),
    ),
    (
        # Five letters, four digits, one letter. Spaces or hyphens allowed between.
        "PAN",
        re.compile(
            r"(?<![A-Za-z0-9])[A-Za-z]{5}[ -]?\d{4}[ -]?[A-Za-z](?![A-Za-z0-9])",
            _FLAGS,
        ),
    ),
    (
        "OTP",
        re.compile(_BEFORE + _OTP_WORD + _AFTER + _GAP_FWD + _NUM_OTP, _FLAGS),
    ),
    (
        "OTP",
        re.compile(_NUM_OTP + _GAP_BACK + _BEFORE + _OTP_WORD + _AFTER, _FLAGS),
    ),
    (
        "ACCOUNT_NO",
        re.compile(_BEFORE + _ACCOUNT_WORD + _AFTER + _GAP_FWD + _NUM_ACCOUNT, _FLAGS),
    ),
    (
        "ACCOUNT_NO",
        re.compile(_NUM_ACCOUNT + _GAP_BACK + _BEFORE + _ACCOUNT_WORD + _AFTER, _FLAGS),
    ),
    (
        # Indian mobile: optional +91, 91 or 0, then 10 digits starting 6 to 9.
        # Grouped as 10, 5-5 or 3-3-4. Runs before Aadhaar, since "91" plus 10 digits
        # is 12 digits.
        "PHONE",
        re.compile(
            r"(?<!\d)(?:(?:\+\s?91|\b91|0)[ -]?)?"
            r"(?:[6-9]\d{9}|[6-9]\d{4}[ -]\d{5}|[6-9]\d{2}[ -]\d{3}[ -]\d{4})(?!\d)",
            _FLAGS,
        ),
    ),
    (
        "AADHAAR",
        re.compile(_BEFORE + _AADHAAR_WORD + _AFTER + _GAP_FWD + _NUM_AADHAAR, _FLAGS),
    ),
    (
        # 12 digits starting 2 to 9, grouped 4-4-4 or not. No checksum is required,
        # because a missed Aadhaar is worse than a redacted look-alike.
        "AADHAAR",
        re.compile(r"(?<!\d)[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?!\d)", _FLAGS),
    ),
)


@dataclass(frozen=True)
class PiiMatch:
    """Where a PII value sits. Holds the type and offsets, never the value."""

    type: str
    start: int
    end: int


def _normalise(text: str) -> str:
    """A same-length copy of ``text`` that the detectors can read.

    Offsets must line up with the original, so the copy changes characters one for
    one: control characters become spaces, and digits from other scripts (for
    example Devanagari) become ASCII digits.
    """
    chars: list[str] = []
    for ch in text:
        if ch.isdecimal() and not ch.isascii():
            chars.append(str(unicodedata.digit(ch)))
        elif unicodedata.category(ch) == "Cc" and ch not in "\n\r\t":
            chars.append(" ")
        else:
            chars.append(ch)
    return "".join(chars)


def _overlaps(claimed: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < c_end and c_start < end for c_start, c_end in claimed)


def find_pii(text: str) -> tuple[PiiMatch, ...]:
    """Every PII match in ``text``, in text order. Placeholders never match."""
    shadow = _normalise(text)
    claimed: list[tuple[int, int]] = []
    found: list[PiiMatch] = []
    for pii_type, pattern in _DETECTORS:
        for match in pattern.finditer(shadow):
            group = "num" if "num" in match.re.groupindex else 0
            start, end = match.span(group)
            if start == end or _overlaps(claimed, start, end):
                continue
            claimed.append((start, end))
            found.append(PiiMatch(pii_type, start, end))
    found.sort(key=lambda m: m.start)
    return tuple(found)


def contains_pii(text: str) -> bool:
    return bool(find_pii(text))


# ---------------------------------------------------------------------------
# Result and entry point
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RedactionResult:
    """What the Orchestrator gets back. The raw complaint is not part of it."""

    redacted_text: str
    manifest: Mapping[str, int]
    request_id: str

    def __repr__(self) -> str:
        return (
            f"RedactionResult(request_id={self.request_id!r}, "
            f"redacted_text={self.redacted_text!r}, manifest={dict(self.manifest)!r})"
        )


def _replace(text: str, matches: tuple[PiiMatch, ...]) -> str:
    parts: list[str] = []
    cursor = 0
    for m in matches:
        parts.append(text[cursor : m.start])
        parts.append(PLACEHOLDERS[m.type])
        cursor = m.end
    parts.append(text[cursor:])
    return "".join(parts)


def _check_usable(raw: object, max_chars: int) -> str:
    if not isinstance(raw, str):
        raise IngressError("NOT_TEXT")
    if not raw.strip():
        raise IngressError("EMPTY_INPUT")
    if len(raw) > max_chars:
        raise IngressError("TOO_LONG")
    if not any(ch.isalnum() for ch in raw):
        raise IngressError("NO_USABLE_TEXT")
    return raw


def redact(
    raw: str,
    *,
    request_id: str | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> RedactionResult:
    """Replace PII in a raw complaint with typed placeholders.

    Raises ``IngressError`` for unusable input or if redaction cannot finish. The raw
    text is never returned, logged, or included in an error. There is no fallback
    that passes unredacted text on.
    """
    text = _check_usable(raw, max_chars)
    if request_id is None:
        request_id = new_request_id()
    elif not is_request_id(request_id):
        raise IngressError("BAD_REQUEST_ID")

    try:
        counts: dict[str, int] = {}
        for _ in range(_MAX_PASSES):
            matches = find_pii(text)
            if not matches:
                break
            for m in matches:
                counts[m.type] = counts.get(m.type, 0) + 1
            text = _replace(text, matches)
        else:
            # Still finding PII after the last pass. Fail closed.
            if find_pii(text):
                raise IngressError("REDACTION_FAILED")
    except IngressError:
        raise
    except Exception:
        # `from None` drops the original exception, which could carry input text.
        raise IngressError("REDACTION_FAILED") from None

    manifest = MappingProxyType({t: counts[t] for t in PII_TYPES if t in counts})
    return RedactionResult(redacted_text=text, manifest=manifest, request_id=request_id)
