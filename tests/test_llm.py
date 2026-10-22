"""Phase 7: Gemini as the Reviewer's model, and how it is wired in.

Everything here runs offline with a fake transport. The one live test is skipped unless a
Gemini key is available.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from agents.compliance import ComplianceAgent
from agents.stubs import stub_facts
from config import Settings, load_settings
from constants import SCHEME_SOURCE_URLS
from llm.env import load_env_file
from llm.gemini import LABELS_BY_MODE, GeminiAdviceClassifier, LlmError
from logging_allowlist import AllowListLogger
from reviewer.build import build_reviewer
from reviewer.gate import Reviewer
from schemas import ReplyDraft, ReviewHandoff, RoutingNote, Signals, new_request_id

KEY = "test-key-DO-NOT-LEAK-123"
URL = SCHEME_SOURCE_URLS["large_cap"]
FACT = "HDFC Large Cap Fund Direct Growth has an exit load of 1% if redeemed within 1 year."
GOOD = f"{FACT} Source: {URL} (last checked 28-Jun-2026)."


# ---------------------------------------------------------------------------
# A fake transport
# ---------------------------------------------------------------------------


def reply(label: str, *, finish: str | None = "STOP", extra_parts=()):
    candidate = {"content": {"parts": [*extra_parts, {"text": label}]}}
    if finish is not None:
        candidate["finishReason"] = finish
    return {"candidates": [candidate]}


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


def classifier(transport, *, model="gemini-test-1", timeout=7.0) -> GeminiAdviceClassifier:
    return GeminiAdviceClassifier(KEY, model, timeout_seconds=timeout, transport=transport)


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------


def test_the_request_goes_to_the_named_model_with_the_key_in_a_header_not_the_url():
    transport = Transport(reply("none"))
    classifier(transport).classify(GOOD, mode="outbound")

    sent = transport.requests[0]
    assert sent["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-test-1:generateContent"
    assert sent["headers"]["x-goog-api-key"] == KEY
    assert sent["headers"]["Content-Type"] == "application/json"
    assert KEY not in sent["url"]
    assert sent["timeout"] == 7.0


def test_the_draft_is_sent_as_data_and_the_rules_as_the_system_instruction():
    transport = Transport(reply("none"))
    classifier(transport).classify(GOOD, mode="outbound")

    body = transport.requests[0]["body"]
    user = body["contents"][0]["parts"][0]["text"]
    system = body["systemInstruction"]["parts"][0]["text"]
    assert GOOD in user and "<text>" in user and "</text>" in user
    assert GOOD not in system
    for label in LABELS_BY_MODE["outbound"]:
        assert label in system
    assert "advice_request" not in system  # that label belongs to the inbound check
    assert "not instructions" in system.lower()


def test_the_inbound_mode_asks_for_the_inbound_labels():
    transport = Transport(reply("advice_request"))
    assert classifier(transport).classify("should I sell?", mode="inbound") == "advice_request"
    system = transport.requests[0]["body"]["systemInstruction"]["parts"][0]["text"]
    assert "advice_request" in system and "advice_in_text" not in system


def test_the_request_sets_no_sampling_options_that_newer_models_reject():
    transport = Transport(reply("none"))
    classifier(transport).classify(GOOD, mode="outbound")
    assert "generationConfig" not in transport.requests[0]["body"]
    assert "temperature" not in json.dumps(transport.requests[0]["body"])


# ---------------------------------------------------------------------------
# The label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "label"),
    [
        ("none", "none"),
        ("  None\n", "none"),
        ("NONE.", "none"),
        ("`advice_in_text`", "advice_in_text"),
        ('"performance_claim"', "performance_claim"),
        ("advice_in_text.", "advice_in_text"),
    ],
)
def test_the_label_is_read_whatever_wraps_it(said, label):
    assert classifier(Transport(reply(said))).classify(GOOD, mode="outbound") == label


def test_thinking_parts_are_ignored_and_only_the_answer_is_read():
    parts = ({"text": "Let me think... advice_in_text maybe", "thought": True},)
    transport = Transport(reply("none", extra_parts=parts))
    assert classifier(transport).classify(GOOD, mode="outbound") == "none"


@pytest.mark.parametrize(
    "said",
    [
        "",
        "   ",
        "Sure! The label is none",
        "none because the text is fine",
        "advice_in_text\nnone",
        "maybe",
        "advice_request",  # an inbound label, not valid for an outbound check
        "NONE_OF_THE_ABOVE",
    ],
)
def test_anything_that_is_not_exactly_one_valid_label_is_an_error(said):
    with pytest.raises(LlmError):
        classifier(Transport(reply(said))).classify(GOOD, mode="outbound")


def test_an_unknown_mode_is_an_error_and_nothing_is_sent():
    transport = Transport(reply("none"))
    with pytest.raises(LlmError):
        classifier(transport).classify(GOOD, mode="sideways")
    assert transport.requests == []


# ---------------------------------------------------------------------------
# Failures never read as "none"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 503])
def test_an_http_error_is_an_error_with_a_fixed_code_and_no_body(status):
    transport = Transport(raw=b'{"error": "secret body text with ' + KEY.encode() + b'"}', status=status)
    with pytest.raises(LlmError) as caught:
        classifier(transport).classify(GOOD, mode="outbound")
    assert f"GEMINI_HTTP_{status}" in str(caught.value)
    assert "secret body" not in str(caught.value) and KEY not in str(caught.value)


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"candidates": []},
        {"candidates": [{}]},
        {"candidates": [{"content": {"parts": []}}]},
        {"promptFeedback": {"blockReason": "SAFETY"}},
        reply("none", finish="MAX_TOKENS"),
        reply("none", finish="SAFETY"),
        reply("none", finish="RECITATION"),
    ],
)
def test_a_blocked_empty_or_cut_off_answer_is_an_error(response):
    with pytest.raises(LlmError):
        classifier(Transport(response)).classify(GOOD, mode="outbound")


@pytest.mark.parametrize("raw", [b"", b"not json", b"[]", b'"none"', b"null"])
def test_a_body_that_is_not_the_expected_json_is_an_error(raw):
    with pytest.raises(LlmError):
        classifier(Transport(raw=raw)).classify(GOOD, mode="outbound")


def test_a_missing_finish_reason_is_accepted():
    assert classifier(Transport(reply("none", finish=None))).classify(GOOD, mode="outbound") == "none"


def test_an_unreachable_service_is_an_error_without_a_message():
    with pytest.raises(LlmError) as caught:
        classifier(Transport(exc=OSError(f"boom {KEY}"))).classify(GOOD, mode="outbound")
    assert KEY not in str(caught.value)


def test_the_default_transport_turns_network_failures_into_errors(monkeypatch):
    def refuse(*args, **kwargs):
        raise urllib.error.URLError(f"no route, key {KEY}")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(LlmError) as caught:
        GeminiAdviceClassifier(KEY, "gemini-test-1").classify(GOOD, mode="outbound")
    assert "GEMINI_UNREACHABLE" in str(caught.value) and KEY not in str(caught.value)


def test_the_default_transport_reports_the_status_of_an_http_error(monkeypatch):
    def reject(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", reject)
    with pytest.raises(LlmError, match="GEMINI_HTTP_429"):
        GeminiAdviceClassifier(KEY, "gemini-test-1").classify(GOOD, mode="outbound")


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["", "   ", "gemini/../../x", "model name", "a?b=c", "x:generateContent"])
def test_a_model_name_that_could_change_the_url_is_rejected(model):
    with pytest.raises(ValueError):
        GeminiAdviceClassifier(KEY, model)


@pytest.mark.parametrize("key", ["", "   ", None])
def test_an_empty_key_is_rejected(key):
    with pytest.raises(ValueError):
        GeminiAdviceClassifier(key, "gemini-test-1")


def test_the_key_is_not_in_the_repr():
    assert KEY not in repr(classifier(Transport(reply("none"))))


@pytest.mark.parametrize("timeout", [0, -1])
def test_a_timeout_must_be_positive(timeout):
    with pytest.raises(ValueError):
        GeminiAdviceClassifier(KEY, "gemini-test-1", timeout_seconds=timeout)


# ---------------------------------------------------------------------------
# Inside the Compliance Agent and the Reviewer
# ---------------------------------------------------------------------------

NOTE = RoutingNote(
    team="Mutual Funds Support",
    priority="P3",
    reason="one off",
    signals=Signals(facts="covered", pattern="one_off", compliance="clean"),
)


def handoff(text: str, round: int = 1) -> ReviewHandoff:
    draft = ReplyDraft(text=text, kind="answer", citation=URL, last_checked="28-Jun-2026")
    return ReviewHandoff(
        request_id=new_request_id(), draft=draft, routing_note=NOTE, facts=stub_facts(), round=round
    )


def gemini_reviewer(transport, data) -> Reviewer:
    agent = ComplianceAgent(corpus=data.corpus, classifier=classifier(transport))
    return Reviewer(outbound_check=agent.outbound)


def test_a_clean_draft_passes_when_gemini_says_none(data):
    transport = Transport(reply("none"))
    result = gemini_reviewer(transport, data)(handoff(GOOD))
    assert result.verdict == "PASS"
    assert len(transport.requests) == 1


def test_gemini_can_fail_a_draft_the_rules_did_not_catch(data):
    result = gemini_reviewer(Transport(reply("advice_in_text")), data)(handoff(GOOD))
    assert result.verdict == "FAIL"
    assert result.failure_types == ("ADVICE_LANGUAGE",)


def test_gemini_can_flag_a_performance_claim(data):
    result = gemini_reviewer(Transport(reply("performance_claim")), data)(handoff(GOOD))
    assert result.failure_types == ("ADVICE_LANGUAGE",)


def test_gemini_saying_none_cannot_remove_a_flag_from_the_rules(data):
    text = f"{FACT} You should buy more units. Source: {URL} (last checked 28-Jun-2026)."
    result = gemini_reviewer(Transport(reply("none")), data)(handoff(text))
    assert "ADVICE_LANGUAGE" in result.failure_types


@pytest.mark.parametrize(
    "transport",
    [
        Transport(reply("looks fine to me")),
        Transport(status=503, raw=b"{}"),
        Transport(exc=OSError("down")),
        Transport({"candidates": []}),
    ],
)
def test_if_gemini_fails_the_draft_is_not_passed(data, transport):
    # REV-17: fail closed.
    result = gemini_reviewer(transport, data)(handoff(GOOD))
    assert result.verdict == "FAIL"
    assert result.failure_types == ("COMPLIANCE_CHECK_FAILED",)


def test_a_draft_with_pii_never_depends_on_gemini(data):
    result = gemini_reviewer(Transport(reply("none")), data)(handoff(f"{GOOD} Call 9876543210."))
    assert "PII_SURVIVED" in result.failure_types


# ---------------------------------------------------------------------------
# The .env loader
# ---------------------------------------------------------------------------


def test_the_env_file_is_read_without_overriding_what_is_already_set(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# a comment\n\nGOOGLE_API_KEY=from-file\nEXISTING=from-file\nexport QUOTED=\"in quotes\"\n"
        "SINGLE='single'\nEMPTY=\nnot a line\n=novalue\n  SPACED = padded  \n",
        encoding="utf-8",
    )
    environ = {"EXISTING": "already"}

    loaded = load_env_file(path, environ)

    assert environ == {
        "EXISTING": "already",
        "GOOGLE_API_KEY": "from-file",
        "QUOTED": "in quotes",
        "SINGLE": "single",
        "SPACED": "padded",
    }
    assert sorted(loaded) == ["GOOGLE_API_KEY", "QUOTED", "SINGLE", "SPACED"]


def test_an_empty_value_in_the_file_does_not_count_as_set(tmp_path):
    path = tmp_path / ".env"
    path.write_text("GOOGLE_API_KEY=\n", encoding="utf-8")
    environ: dict[str, str] = {}
    assert load_env_file(path, environ) == [] and environ == {}


def test_a_missing_env_file_is_not_an_error(tmp_path):
    assert load_env_file(tmp_path / "nope.env", {}) == []


def test_loading_reports_names_never_values(tmp_path):
    path = tmp_path / ".env"
    path.write_text(f"GOOGLE_API_KEY={KEY}\n", encoding="utf-8")
    assert KEY not in repr(load_env_file(path, {}))


# ---------------------------------------------------------------------------
# build_reviewer
# ---------------------------------------------------------------------------


def settings(provider="gemini", model="gemini-test-1") -> Settings:
    return Settings(llm={"reviewer": {"provider": provider, "model": model}})


def test_gemini_is_wired_in_from_the_settings_and_the_key(data):
    transport = Transport(reply("advice_in_text"))
    reviewer = build_reviewer(
        settings(), corpus=data.corpus, environ={"GOOGLE_API_KEY": KEY}, transport=transport
    )

    assert reviewer(handoff(GOOD)).failure_types == ("ADVICE_LANGUAGE",)
    assert transport.requests[0]["url"].endswith("/models/gemini-test-1:generateContent")
    assert transport.requests[0]["headers"]["x-goog-api-key"] == KEY


def test_the_gemini_key_name_is_also_accepted(data):
    transport = Transport(reply("none"))
    reviewer = build_reviewer(
        settings(), corpus=data.corpus, environ={"GEMINI_API_KEY": KEY}, transport=transport
    )
    assert reviewer(handoff(GOOD)).verdict == "PASS"


def test_the_agent_timeout_from_the_settings_is_used(data):
    transport = Transport(reply("none"))
    build_reviewer(
        Settings(
            llm={"reviewer": {"provider": "gemini", "model": "gemini-test-1"}},
            limits={"agent_timeout_seconds": 11},
        ),
        corpus=data.corpus,
        environ={"GOOGLE_API_KEY": KEY},
        transport=transport,
    )(handoff(GOOD))
    assert transport.requests[0]["timeout"] == 11


def test_a_missing_key_is_a_clear_error_not_a_silently_weaker_gate(data):
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        build_reviewer(settings(), corpus=data.corpus, environ={})


def test_an_empty_key_counts_as_missing(data):
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        build_reviewer(settings(), corpus=data.corpus, environ={"GOOGLE_API_KEY": "  "})


def test_provider_none_runs_the_reviewer_on_its_rules_alone(data):
    reviewer = build_reviewer(settings(provider="none", model=""), corpus=data.corpus, environ={})
    assert reviewer(handoff(GOOD)).verdict == "PASS"
    assert "ADVICE_LANGUAGE" in reviewer(handoff(f"{FACT} You should buy more units. Source: {URL} (last checked 28-Jun-2026).")).failure_types


def test_an_undecided_reviewer_model_is_a_clear_error(data):
    with pytest.raises(RuntimeError, match="llm.reviewer"):
        build_reviewer(Settings(), corpus=data.corpus, environ={})


def test_an_unsupported_provider_is_a_clear_error(data):
    with pytest.raises(ValueError, match="provider"):
        build_reviewer(settings(provider="mystery"), corpus=data.corpus, environ={"GOOGLE_API_KEY": KEY})


def test_the_reviewer_built_here_logs_through_the_allow_list(data):
    records: list[dict] = []
    logger = AllowListLogger(sink=records.append)
    reviewer = build_reviewer(
        settings(), corpus=data.corpus, logger=logger,
        environ={"GOOGLE_API_KEY": KEY}, transport=Transport(reply("none")),
    )
    reviewer(handoff(GOOD))
    assert [r["event"] for r in records] == ["reviewer"]
    assert KEY not in json.dumps(records)


def test_the_real_settings_choose_gemini_for_the_reviewer(data):
    chosen = load_settings().llm.for_use("reviewer")
    assert chosen.provider == "gemini"
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        build_reviewer(load_settings(), corpus=data.corpus, environ={})


# ---------------------------------------------------------------------------
# Live (skipped without a key)
# ---------------------------------------------------------------------------


def _live_key() -> str | None:
    import os

    environ = dict(os.environ)
    load_env_file(environ=environ)
    return environ.get("GOOGLE_API_KEY") or environ.get("GEMINI_API_KEY")


@pytest.mark.live
@pytest.mark.skipif(not _live_key(), reason="no GOOGLE_API_KEY or GEMINI_API_KEY available")
def test_live_gemini_labels_a_clean_fact_none_and_advice_as_advice():
    model = load_settings().llm.for_use("reviewer").model
    live = GeminiAdviceClassifier(_live_key(), model)
    try:
        assert live.classify(GOOD, mode="outbound") == "none"
        assert (
            live.classify("You should switch to this fund and hold it for ten years.", mode="outbound")
            == "advice_in_text"
        )
        assert live.classify("This fund will give you 18% returns next year.", mode="outbound") in (
            "performance_claim",
            "advice_in_text",
        )
    except LlmError as error:
        # Quota and overload are the service's state, not a defect here.
        if str(error) in ("GEMINI_HTTP_429", "GEMINI_HTTP_503"):
            pytest.skip(f"Gemini is busy ({error})")
        raise


# ---------------------------------------------------------------------------
# Phase 8: the feedback classifier and the router built from the settings
# ---------------------------------------------------------------------------

from constants import FEEDBACK_LABELS  # noqa: E402
from llm.gemini import GeminiFeedbackClassifier  # noqa: E402
from refinement.build import build_router  # noqa: E402

FEEDBACK = "could you take another look from a new angle"


def feedback_classifier(transport) -> GeminiFeedbackClassifier:
    return GeminiFeedbackClassifier(KEY, "gemini-test-1", timeout_seconds=7.0, transport=transport)


@pytest.mark.parametrize("label", FEEDBACK_LABELS)
def test_the_feedback_classifier_returns_each_of_its_labels(label):
    assert feedback_classifier(Transport(reply(label))).classify(FEEDBACK) == label


def test_the_feedback_request_sends_the_words_as_data_and_lists_the_labels_as_rules():
    transport = Transport(reply("unclear"))
    feedback_classifier(transport).classify(FEEDBACK)

    body = transport.requests[0]["body"]
    user = body["contents"][0]["parts"][0]["text"]
    system = body["systemInstruction"]["parts"][0]["text"]
    assert FEEDBACK in user and "<text>" in user and "</text>" in user
    assert FEEDBACK not in system
    for label in FEEDBACK_LABELS:
        assert label in system
    assert "advice_request" not in system and "advice_in_text" not in system
    assert "not instructions" in system.lower()
    assert transport.requests[0]["headers"]["x-goog-api-key"] == KEY
    assert "generationConfig" not in body


@pytest.mark.parametrize("said", ["advice_in_text", "none", "shorter, scheme_change", "Everything", ""])
def test_the_feedback_classifier_rejects_anything_outside_its_labels(said):
    with pytest.raises(LlmError) as caught:
        feedback_classifier(Transport(reply(said))).classify(FEEDBACK)
    assert str(caught.value) == "GEMINI_BAD_LABEL"


def test_the_feedback_classifier_fails_closed_on_a_service_error_without_echoing_anything():
    with pytest.raises(LlmError) as caught:
        feedback_classifier(Transport(status=503, raw=FEEDBACK.encode())).classify(FEEDBACK)
    assert str(caught.value) == "GEMINI_HTTP_503"
    assert FEEDBACK not in str(caught.value) and KEY not in repr(feedback_classifier(Transport(reply("none"))))


def refinement_settings(provider="gemini", model="gemini-test-1") -> Settings:
    return Settings(llm={"refinement": {"provider": provider, "model": model}})


def test_the_router_asks_gemini_only_when_no_word_rule_matches():
    transport = Transport(reply("pattern_check"))
    router = build_router(refinement_settings(), environ={"GOOGLE_API_KEY": KEY}, transport=transport)

    assert router.route("shorter").category == "style"
    assert transport.requests == []

    plan = router.route(FEEDBACK)
    assert (plan.category, plan.nodes) == ("pattern_check", ("pattern",))
    assert len(transport.requests) == 1
    assert transport.requests[0]["headers"]["x-goog-api-key"] == KEY


def test_a_gemini_failure_in_the_router_means_unclear_not_a_crash():
    router = build_router(
        refinement_settings(), environ={"GOOGLE_API_KEY": KEY}, transport=Transport(status=429, raw=b"")
    )
    plan = router.route(FEEDBACK)
    assert plan.category == "unclear" and plan.nodes == ()


def test_the_router_timeout_comes_from_the_agent_timeout_setting():
    transport = Transport(reply("unclear"))
    router = build_router(
        Settings(
            llm={"refinement": {"provider": "gemini", "model": "gemini-test-1"}},
            limits={"agent_timeout_seconds": 9},
        ),
        environ={"GEMINI_API_KEY": KEY},
        transport=transport,
    )
    router.route(FEEDBACK)
    assert transport.requests[0]["timeout"] == 9


def test_provider_none_gives_a_router_with_the_word_rules_alone():
    router = build_router(refinement_settings(provider="none", model=""), environ={})
    assert router.route("shorter").category == "style"
    assert router.route(FEEDBACK).category == "unclear"


def test_a_missing_key_for_the_refinement_classifier_is_a_clear_error():
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        build_router(refinement_settings(), environ={})


def test_an_undecided_refinement_model_is_a_clear_error():
    with pytest.raises(RuntimeError, match="llm.refinement"):
        build_router(Settings(), environ={})


def test_an_unsupported_refinement_provider_is_a_clear_error():
    with pytest.raises(ValueError, match="provider"):
        build_router(refinement_settings(provider="mystery"), environ={"GOOGLE_API_KEY": KEY})


def test_the_real_settings_choose_gemini_for_refinement():
    assert load_settings().llm.for_use("refinement").provider == "gemini"
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        build_router(load_settings(), environ={})
