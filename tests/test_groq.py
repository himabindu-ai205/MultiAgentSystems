"""Groq calls stay tiny: the free tier is about 8k tokens per minute.

Classifiers return one short label. The drafter rewrites at most three sentences. Every
request caps completion tokens, truncates the input, turns reasoning down on gpt-oss, and
keeps the prompt to a few labels. A first run that hits Groq three or four times must stay
well under the per-minute cap.
"""

from __future__ import annotations

import json

import pytest

from config import Settings
from constants import SECTION_TITLES, THEME_IDS
from llm.env import require_groq_key
from llm.groq import (
    CLASSIFIER_MAX_COMPLETION_TOKENS,
    CLASSIFIER_MAX_INPUT_CHARS,
    DRAFTER_MAX_COMPLETION_TOKENS,
    GroqAdviceClassifier,
    GroqDraftWriter,
    GroqSectionClassifier,
    GroqThemeClassifier,
    LlmError,
)
from orchestrator.drafter import DraftRequest

KEY = "test-groq-key-DO-NOT-LEAK-123"
OSS = "openai/gpt-oss-120b"
LLAMA = "llama-3.1-8b-instant"
URL = "https://api.groq.com/openai/v1/chat/completions"


def groq_reply(content: str) -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}


class Transport:
    def __init__(self, response=None, *, status: int = 200, raw: bytes | None = None, exc=None) -> None:
        self.response, self.status, self.raw, self.exc = response, status, raw, exc
        self.requests: list[dict] = []

    def __call__(self, url, headers, body, timeout):
        self.requests.append({"url": url, "headers": dict(headers), "body": json.loads(body), "timeout": timeout})
        if self.exc:
            raise self.exc
        payload = self.raw if self.raw is not None else json.dumps(self.response).encode()
        return self.status, payload


def prompt_chars(body: dict) -> int:
    return sum(len(m.get("content") or "") for m in body["messages"])


# ---------------------------------------------------------------------------
# Caps
# ---------------------------------------------------------------------------


def test_a_classifier_call_caps_completion_tokens_and_keeps_the_prompt_tiny():
    transport = Transport(groq_reply("Exit load"))
    GroqSectionClassifier(KEY, OSS, transport=transport).classify("exit load of the large cap fund")

    body = transport.requests[0]["body"]
    assert body["max_completion_tokens"] == CLASSIFIER_MAX_COMPLETION_TOKENS
    assert CLASSIFIER_MAX_COMPLETION_TOKENS <= 32
    assert body["temperature"] == 0
    assert prompt_chars(body) <= 400
    assert "Exit load" in body["messages"][0]["content"]


def test_a_long_complaint_is_truncated_so_one_call_cannot_eat_the_budget():
    transport = Transport(groq_reply("none"))
    GroqThemeClassifier(KEY, OSS, transport=transport).classify("fees " + ("word " * 2000))

    user = transport.requests[0]["body"]["messages"][-1]["content"]
    assert len(user) <= CLASSIFIER_MAX_INPUT_CHARS
    assert CLASSIFIER_MAX_INPUT_CHARS <= 400


def test_gpt_oss_turns_reasoning_down_and_does_not_ask_for_the_chain_of_thought():
    transport = Transport(groq_reply("none"))
    GroqAdviceClassifier(KEY, OSS, transport=transport).classify("should I sell?", mode="inbound")

    body = transport.requests[0]["body"]
    assert body["reasoning_effort"] == "low"
    assert body["include_reasoning"] is False


def test_a_non_reasoning_model_does_not_send_reasoning_fields():
    transport = Transport(groq_reply("none"))
    GroqAdviceClassifier(KEY, LLAMA, transport=transport).classify("should I sell?", mode="inbound")

    body = transport.requests[0]["body"]
    assert "reasoning_effort" not in body
    assert "include_reasoning" not in body


def test_the_drafter_caps_completion_tokens_below_a_short_reply():
    transport = Transport(groq_reply("Exit load is 1% if redeemed within 1 year."))
    GroqDraftWriter(KEY, OSS, transport=transport).write(
        DraftRequest(
            kind="answer",
            style="standard",
            max_sentences=3,
            facts=("Exit load is 1% if redeemed within 1 year.",),
            citation="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
            last_checked="28-Jun-2026",
            scheme_names=("HDFC Large Cap Fund",),
            section_titles=("Exit load",),
            template="HDFC Large Cap Fund has an exit load of 1% if redeemed within 1 year.",
        )
    )

    body = transport.requests[0]["body"]
    assert body["max_completion_tokens"] == DRAFTER_MAX_COMPLETION_TOKENS
    assert DRAFTER_MAX_COMPLETION_TOKENS <= 96


def test_four_classifier_calls_plus_a_draft_stay_under_the_8k_tpm_budget():
    """A first run can hit Groq for Facts, Pattern, Compliance and the drafter."""
    transport = Transport(groq_reply("none"))
    complaint = "what is the exit load of hdfc large cap? " + ("please check " * 80)
    GroqSectionClassifier(KEY, OSS, transport=transport).classify(complaint)
    GroqThemeClassifier(KEY, OSS, transport=transport).classify(complaint)
    GroqAdviceClassifier(KEY, OSS, transport=transport).classify(complaint, mode="inbound")
    GroqDraftWriter(KEY, OSS, transport=transport).write(
        DraftRequest(
            kind="answer",
            style="standard",
            max_sentences=3,
            facts=("x",),
            citation=None,
            last_checked=None,
            scheme_names=(),
            section_titles=(),
            template="I can't answer this from the HDFC scheme information I have.",
        )
    )

    prompt = sum(prompt_chars(r["body"]) for r in transport.requests)
    completion_cap = sum(r["body"]["max_completion_tokens"] for r in transport.requests)
    # ~4 chars per token is a safe over-estimate of prompt tokens.
    assert prompt / 4 + completion_cap < 2000


# ---------------------------------------------------------------------------
# Request shape and fail-closed
# ---------------------------------------------------------------------------


def test_the_request_goes_to_groq_with_the_key_in_a_header_not_the_url():
    transport = Transport(groq_reply("none"))
    GroqSectionClassifier(KEY, OSS, timeout_seconds=9.0, transport=transport).classify("exit load")

    sent = transport.requests[0]
    assert sent["url"] == URL
    assert sent["headers"]["Authorization"] == f"Bearer {KEY}"
    assert KEY not in sent["url"]
    assert sent["timeout"] == 9.0
    assert sent["body"]["model"] == OSS


def test_the_section_classifier_returns_a_known_title_or_nothing():
    assert GroqSectionClassifier(KEY, OSS, transport=Transport(groq_reply("Exit load"))).classify("q") == "Exit load"
    assert GroqSectionClassifier(KEY, OSS, transport=Transport(groq_reply("none"))).classify("q") is None
    assert GroqSectionClassifier(KEY, OSS, transport=Transport(groq_reply("banana"))).classify("q") is None
    assert set(SECTION_TITLES) >= {"Exit load"}


def test_the_theme_classifier_returns_a_known_id_or_nothing():
    assert GroqThemeClassifier(KEY, OSS, transport=Transport(groq_reply("fees_brokerage"))).classify("q") == "fees_brokerage"
    assert GroqThemeClassifier(KEY, OSS, transport=Transport(groq_reply("none"))).classify("q") is None
    assert "fees_brokerage" in THEME_IDS


def test_compliance_raises_on_an_unusable_label_so_the_agent_fails_closed():
    with pytest.raises(LlmError, match="GROQ_BAD_LABEL"):
        GroqAdviceClassifier(KEY, OSS, transport=Transport(groq_reply("maybe"))).classify("q", mode="inbound")


def test_http_errors_and_a_dead_connection_are_named_codes():
    with pytest.raises(LlmError, match="GROQ_HTTP_429"):
        GroqAdviceClassifier(KEY, OSS, transport=Transport(status=429, raw=b"")).classify("q", mode="outbound")
    with pytest.raises(LlmError, match="GROQ_UNREACHABLE"):
        GroqAdviceClassifier(KEY, OSS, transport=Transport(exc=TimeoutError())).classify("q", mode="outbound")


def test_the_key_is_never_kept_on_the_client_repr():
    assert KEY not in repr(GroqSectionClassifier(KEY, OSS, transport=Transport(groq_reply("none"))))


# ---------------------------------------------------------------------------
# Key + wiring
# ---------------------------------------------------------------------------


def test_a_missing_groq_key_is_a_clear_error():
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        require_groq_key({}, setting="llm.facts.provider")
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        require_groq_key({"GROQ_API_KEY": "  "}, setting="llm.facts.provider")
    assert require_groq_key({"GROQ_API_KEY": KEY}, setting="llm.facts.provider") == KEY


def groq_only() -> Settings:
    return Settings(
        llm={
            "provider": "groq",
            "model": OSS,
            "reviewer": {"provider": "none"},
            "refinement": {"provider": "none"},
        }
    )


def test_build_app_with_groq_sends_capped_calls(data):
    from app import build_app

    transport = Transport(groq_reply("none"))
    app = build_app(groq_only(), data=data, environ={"GROQ_API_KEY": KEY}, groq_transport=transport)
    app.orchestrator.submit("What is the exit load of the HDFC Large Cap fund?")

    assert transport.requests  # at least Compliance and the drafter
    for sent in transport.requests:
        assert sent["body"]["max_completion_tokens"] <= DRAFTER_MAX_COMPLETION_TOKENS
        assert sent["body"]["temperature"] == 0
        assert prompt_chars(sent["body"]) < 1500


def test_build_app_with_groq_and_no_key_fails_clearly(data):
    from app import build_app

    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        build_app(groq_only(), data=data, environ={})
