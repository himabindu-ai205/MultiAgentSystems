"""Shared text helpers. One sentence counter serves the drafter, Compliance and the Reviewer."""

from __future__ import annotations

import pytest

from textutil import count_sentences, format_last_checked, split_sentences, urls_in

SOURCE = "Source: https://groww.in/mutual-funds/x (last checked 28-Jun-2026)."


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("One sentence.", 1),
        ("First. Second. Third.", 3),
        ("Is it covered? Yes. Please check.", 3),
        # A full stop inside a number, a name or a link is not a sentence end.
        ("The expense ratio is 1.04%. The NAV is 226.915.", 2),
        (f"The fund is listed. {SOURCE}", 2),
        # Abbreviations do not end a sentence (edge case REV-09).
        (f"The minimum SIP is Rs. 100. The expense ratio is 1.04%. {SOURCE}", 3),
        (f"The fee is e.g. 1.04% of the amount. Cr. 5 is not a sentence end. {SOURCE}", 3),
        (f"Mr. Chirag Setalvad manages the fund. Rs. 500 is the minimum lump sum. {SOURCE}", 3),
        ("It is i.e. the same. Dr. Rao agrees. Mrs. Rao agrees.", 3),
        # A real sentence end after a word that merely looks like an abbreviation.
        ("It was a short note. Another one follows.", 2),
    ],
)
def test_sentences_are_counted_the_way_a_reader_counts_them(text, expected):
    assert count_sentences(text) == expected, split_sentences(text)


def test_an_abbreviation_stays_attached_to_the_text_after_it():
    assert split_sentences("Mr. Setalvad is the manager. He joined in 2013.") == [
        "Mr. Setalvad is the manager.",
        "He joined in 2013.",
    ]


def test_a_new_line_still_starts_a_new_sentence():
    assert count_sentences("First line\nSecond line") == 2


def test_urls_lose_trailing_punctuation_only():
    assert urls_in("See https://groww.in/a-b. Then (https://groww.in/c).") == [
        "https://groww.in/a-b",
        "https://groww.in/c",
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-06-28T06:28:55+00:00", "28-Jun-2026"),
        ("2026-06-28T23:30:00-05:00", "29-Jun-2026"),
        ("2026-01-02T00:00:00Z", "02-Jan-2026"),
    ],
)
def test_last_checked_is_the_utc_date(value, expected):
    assert format_last_checked(value) == expected


@pytest.mark.parametrize("value", [None, "", "  ", "not a date", 20260628])
def test_last_checked_gives_none_for_anything_unusable(value):
    assert format_last_checked(value) is None
