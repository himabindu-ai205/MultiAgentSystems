"""Phase 1: Ingress redaction (Architecture section 5)."""

import json
import logging
from pathlib import Path

import pytest

from constants import PII_TYPES
from ingress import redactor
from ingress.redactor import IngressError, contains_pii, find_pii, redact
from schemas import new_request_id

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLES = json.loads((FIXTURES / "pii_samples.json").read_text(encoding="utf-8"))["samples"]


# ---------------------------------------------------------------------------
# Synthetic samples
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sample", SAMPLES, ids=[s["id"] for s in SAMPLES])
def test_every_synthetic_sample_is_redacted_in_full(sample):
    result = redact(sample["text"])
    for value in sample["raw_values"]:
        assert value not in result.redacted_text
    assert not contains_pii(result.redacted_text)
    if sample["type"] != "MULTI":
        assert sample["type"] in result.manifest


def test_multi_pii_sample_reports_each_type_once():
    sample = next(s for s in SAMPLES if s["id"] == "multi_1")
    result = redact(sample["text"])
    assert dict(result.manifest) == {"PAN": 1, "PHONE": 1, "EMAIL": 1}
    assert "[PAN]" in result.redacted_text
    assert "[PHONE]" in result.redacted_text
    assert "[EMAIL]" in result.redacted_text


def test_the_rest_of_the_message_is_kept():
    result = redact("My withdrawal is stuck. My PAN is ABCDE1234F, please check why it failed.")
    assert result.redacted_text == (
        "My withdrawal is stuck. My PAN is [PAN], please check why it failed."
    )


# ---------------------------------------------------------------------------
# Format variants, per type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, pii_type",
    [
        # PAN
        ("ABCDE1234F", "PAN"),
        ("abcde1234f", "PAN"),
        ("abcde 1234 f", "PAN"),
        ("ABCDE-1234-F", "PAN"),
        ("PAN:ABCDE1234F.", "PAN"),
        # Aadhaar
        ("2345 6789 0123", "AADHAAR"),
        ("2345-6789-0123", "AADHAAR"),
        ("234567890123", "AADHAAR"),
        ("my aadhaar is 1234 5678 9012", "AADHAAR"),
        ("uid: 123456789012", "AADHAAR"),
        # Phone
        ("9876543210", "PHONE"),
        ("+919876543210", "PHONE"),
        ("+91 9876543210", "PHONE"),
        ("+91-98765-43210", "PHONE"),
        ("91 98765 43210", "PHONE"),
        ("09876543210", "PHONE"),
        ("98765 43210", "PHONE"),
        ("98765-43210", "PHONE"),
        ("987 654 3210", "PHONE"),
        # Email
        ("test.user@example.com", "EMAIL"),
        ("a_b+c@mail.co.in", "EMAIL"),
        ("name at gmail dot com", "EMAIL"),
        ("name (at) gmail (dot) com", "EMAIL"),
        # OTP
        ("OTP 482913", "OTP"),
        ("otp is 4829", "OTP"),
        ("the OTP I received was 482913 and it expired", "OTP"),
        ("verification code: 12345678", "OTP"),
        ("482913 is my otp", "OTP"),
        ("my pin 1234", "OTP"),
        # Account number
        ("account number 123456789012", "ACCOUNT_NO"),
        ("a/c no 123456789", "ACCOUNT_NO"),
        ("acct: 1234-5678-9012-3456", "ACCOUNT_NO"),
        ("my bank account 12345678901234", "ACCOUNT_NO"),
        ("123456789012 is my account", "ACCOUNT_NO"),
    ],
)
def test_format_variants_are_redacted(text, pii_type):
    result = redact("Please help. " + text + " Thanks.")
    assert pii_type in result.manifest, result.redacted_text
    assert f"[{pii_type}]" in result.redacted_text
    assert not contains_pii(result.redacted_text)


@pytest.mark.parametrize("position", ["start", "end", "alone"])
def test_pii_at_the_start_and_end_of_a_message(position):
    pan = "ABCDE1234F"
    text = {"start": f"{pan} is my PAN", "end": f"my PAN is {pan}", "alone": pan}[position]
    result = redact(text)
    assert pan not in result.redacted_text
    assert result.manifest["PAN"] == 1


def test_digits_from_other_scripts_are_read_as_digits():
    # Devanagari digits, same value as 9876543210.
    result = redact("मेरा नंबर ९८७६५४३२१० है")
    assert "९८७६५४३२१०" not in result.redacted_text
    assert result.manifest["PHONE"] == 1


def test_control_characters_do_not_hide_pii():
    result = redact("my PAN is ABCDE1234F\x00 ok")
    assert "ABCDE1234F" not in result.redacted_text


# ---------------------------------------------------------------------------
# Several values, repeats, mixed
# ---------------------------------------------------------------------------


def test_repeated_value_is_counted_each_time():
    result = redact("PAN ABCDE1234F again ABCDE1234F and once more ABCDE1234F")
    assert result.manifest["PAN"] == 3
    assert "ABCDE1234F" not in result.redacted_text


def test_all_six_types_in_one_message():
    result = redact(
        "PAN ABCDE1234F, phone 9876543210, mail a@b.com, OTP 482913, "
        "account number 123456789012, aadhaar 2345 6789 0123."
    )
    assert dict(result.manifest) == {
        "PAN": 1,
        "AADHAAR": 1,
        "PHONE": 1,
        "EMAIL": 1,
        "OTP": 1,
        "ACCOUNT_NO": 1,
    }


def test_phone_with_country_code_is_not_taken_as_aadhaar():
    result = redact("call +91 98765 43210")
    assert dict(result.manifest) == {"PHONE": 1}


def test_manifest_follows_the_order_of_the_pii_vocabulary():
    result = redact("a@b.com and ABCDE1234F")
    assert list(result.manifest) == [t for t in PII_TYPES if t in result.manifest]


# ---------------------------------------------------------------------------
# Things that must NOT be redacted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "The app crashes every time I open the chart.",
        "Exit load is 1% if redeemed within 1 year.",
        "Expense ratio is 1.04% and the NAV is 245.1234 on 25-Jun-2026.",
        "Version 12.4.1 is worse than the old one.",
        "I invested 5000 rupees and lost 300.",
        "Order 12345 failed at 10:45 on 04/07/2026.",
        "Rated 1 star, 3 times in 2 weeks.",
        "मेरा पैसा अटक गया है, कृपया मदद करें",
        "paisa nahi aaya, bahut bura app hai",
    ],
)
def test_ordinary_text_is_left_unchanged(text):
    result = redact(text)
    assert result.redacted_text == text
    assert dict(result.manifest) == {}


def test_no_pii_gives_an_empty_manifest():
    assert dict(redact("nothing sensitive here").manifest) == {}


def test_a_placeholder_typed_by_the_user_is_plain_text():
    result = redact("I wrote [PAN] and [PHONE] in the form")
    assert result.redacted_text == "I wrote [PAN] and [PHONE] in the form"
    assert dict(result.manifest) == {}


def test_a_long_number_without_a_context_word_is_not_an_account_number():
    result = redact("transaction reference 9988776655443322")
    assert "ACCOUNT_NO" not in result.manifest


# ---------------------------------------------------------------------------
# Idempotence and detector reuse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sample", SAMPLES, ids=[s["id"] for s in SAMPLES])
def test_redacting_twice_changes_nothing(sample):
    once = redact(sample["text"])
    twice = redact(once.redacted_text)
    assert twice.redacted_text == once.redacted_text
    assert dict(twice.manifest) == {}


def test_find_pii_returns_types_and_offsets_only():
    text = "my PAN is ABCDE1234F"
    (match,) = find_pii(text)
    assert match.type == "PAN"
    assert text[match.start : match.end] == "ABCDE1234F"
    assert "ABCDE1234F" not in repr(match)


def test_contains_pii_is_false_for_placeholders():
    assert not contains_pii("my PAN is [PAN] and phone [PHONE]")


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


def test_result_carries_a_request_id():
    result = redact("hello there")
    assert len(result.request_id) == 36


def test_a_given_request_id_is_kept():
    rid = new_request_id()
    assert redact("hello there", request_id=rid).request_id == rid


def test_a_bad_request_id_is_rejected():
    with pytest.raises(IngressError) as exc:
        redact("hello there", request_id="not-a-uuid")
    assert exc.value.code == "BAD_REQUEST_ID"


def test_result_is_read_only():
    result = redact("PAN ABCDE1234F")
    with pytest.raises(Exception):
        result.redacted_text = "x"  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.manifest["PAN"] = 5  # type: ignore[index]


def test_manifest_has_counts_only():
    result = redact("PAN ABCDE1234F and phone 9876543210")
    assert all(isinstance(v, int) for v in result.manifest.values())
    assert set(result.manifest) <= set(PII_TYPES)


# ---------------------------------------------------------------------------
# Unusable input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, code",
    [
        ("", "EMPTY_INPUT"),
        ("   \n\t ", "EMPTY_INPUT"),
        ("!!!", "NO_USABLE_TEXT"),
        ("👍👍", "NO_USABLE_TEXT"),
        (None, "NOT_TEXT"),
        (12345, "NOT_TEXT"),
        (b"bytes", "NOT_TEXT"),
    ],
)
def test_unusable_input_is_rejected_with_a_code(raw, code):
    with pytest.raises(IngressError) as exc:
        redact(raw)  # type: ignore[arg-type]
    assert exc.value.code == code


def test_over_long_input_is_rejected_not_truncated():
    with pytest.raises(IngressError) as exc:
        redact("a" * 51, max_chars=50)
    assert exc.value.code == "TOO_LONG"


def test_input_at_the_limit_is_accepted():
    assert redact("a" * 50, max_chars=50).redacted_text == "a" * 50


def test_short_complaints_are_accepted():
    assert redact("bad app").redacted_text == "bad app"


def test_the_limit_comes_from_settings():
    from config import load_settings

    assert load_settings().limits.max_complaint_chars == redactor.DEFAULT_MAX_CHARS


# ---------------------------------------------------------------------------
# Raw values never leak through logs, errors or repr
# ---------------------------------------------------------------------------

LEAK_TEXT = "PAN ABCDE1234F, phone 9123456780, mail test.user@example.com, OTP 482913"
LEAK_VALUES = ["ABCDE1234F", "9123456780", "test.user@example.com", "482913"]


def test_repr_of_the_result_has_no_raw_value():
    text = repr(redact(LEAK_TEXT))
    for value in LEAK_VALUES:
        assert value not in text


def test_no_log_line_is_written_while_redacting(caplog):
    with caplog.at_level(logging.DEBUG):
        redact(LEAK_TEXT)
    assert caplog.records == []


def test_manifest_and_error_text_have_no_raw_value():
    manifest_text = json.dumps(dict(redact(LEAK_TEXT).manifest))
    for value in LEAK_VALUES:
        assert value not in manifest_text
    with pytest.raises(IngressError) as exc:
        redact("   ")
    assert str(exc.value) == "The complaint is empty."


def test_a_detector_failure_gives_a_safe_error_and_no_fallback(monkeypatch):
    def boom(text):
        raise ValueError(f"cannot parse: {text}")

    monkeypatch.setattr(redactor, "find_pii", boom)
    with pytest.raises(IngressError) as exc:
        redact(LEAK_TEXT)
    assert exc.value.code == "REDACTION_FAILED"
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__ is True
    for value in LEAK_VALUES:
        assert value not in str(exc.value)
        assert value not in repr(exc.value)


def test_redaction_fails_closed_if_pii_remains(monkeypatch):
    # A broken replacement step that leaves the text unchanged must not pass it on.
    monkeypatch.setattr(redactor, "_replace", lambda text, matches: text)
    with pytest.raises(IngressError) as exc:
        redact(LEAK_TEXT)
    assert exc.value.code == "REDACTION_FAILED"


# ---------------------------------------------------------------------------
# Real reviews
# ---------------------------------------------------------------------------


def test_real_reviews_are_rarely_touched(data):
    """Counts how many of the 1,650 real reviews change. They contain no PII, so each
    change is a false positive. The threshold is a proposal (eval Q9), not a rule."""
    reviews = list(data.themes.reviews.values())
    assert len(reviews) == 1650
    touched = []
    for review in reviews:
        try:
            result = redact(review.text, max_chars=10**6)
        except IngressError:
            continue  # empty or symbol-only reviews are rejected, not altered
        if result.redacted_text != review.text:
            touched.append(review.review_id)
    assert len(touched) <= len(reviews) * 0.01, f"{len(touched)} reviews touched"
