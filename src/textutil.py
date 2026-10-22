"""Small text helpers shared by the Compliance Agent, the drafter and the Reviewer.

One definition of "a sentence" keeps the drafter's length check and the Reviewer's
length check from disagreeing.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

# A sentence ends at . ! or ? followed by space and a capital letter, digit, rupee sign,
# quote or bracket. A full stop inside "groww.in", "226.915" or "Rs 1.25" is not a break.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\u20b9\"'(\[])|\n+")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_URL = re.compile(r"https?://[^\s)\]>\"']+")


# A piece that ends in one of these is not a finished sentence, so the next piece belongs
# to it ("Rs. 100", "e.g. 1.04%", "Mr. Setalvad"). "No." and "etc." are left out on
# purpose: they often do end a sentence.
_ABBREVIATIONS = ("rs.", "re.", "e.g.", "i.e.", "cr.", "mr.", "mrs.", "ms.", "dr.", "vs.", "approx.", "ltd.", "pvt.")


def _ends_with_abbreviation(piece: str) -> bool:
    lowered = piece.lower()
    for abbreviation in _ABBREVIATIONS:
        if lowered.endswith(abbreviation):
            before = lowered[: -len(abbreviation)]
            if not before or not before[-1].isalnum():
                return True
    return False


def split_sentences(text: str) -> list[str]:
    pieces = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s and s.strip()]
    sentences: list[str] = []
    carry = ""
    for piece in pieces:
        piece = f"{carry} {piece}" if carry else piece
        if _ends_with_abbreviation(piece):
            carry = piece
        else:
            sentences.append(piece)
            carry = ""
    if carry:
        sentences.append(carry)
    return sentences


def count_sentences(text: str) -> int:
    return len(split_sentences(text))


def numbers_in(text: str) -> frozenset[str]:
    """Every number written in the text, for example ``{"1", "20", "226.915"}``."""
    return frozenset(match.group().rstrip(",") for match in _NUMBER.finditer(text))


def urls_in(text: str) -> list[str]:
    """Every link in the text, in order, without trailing punctuation."""
    return [match.group().rstrip(".,;:!?") for match in _URL.finditer(text)]


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def format_last_checked(value: object) -> str | None:
    """``2026-06-28T06:28:55+00:00`` becomes ``28-Jun-2026`` (the UTC date).

    The drafter writes this date into a reply and the Reviewer checks for it, so both
    use this one function.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    day: date = moment.date()
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"
