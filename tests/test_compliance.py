"""Phase 4 tests: the Compliance Agent in inbound and outbound mode."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from agents.compliance import (
    ComplianceAgent,
    hard_fail_types,
    has_pii,
    is_advice_request,
    load_rules,
    run_compliance,
    tax_sentences,
)
from schemas import ComplianceResult

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def agent(data):
    return ComplianceAgent(corpus=data.corpus)


def _advice_prompts() -> list[str]:
    groups = json.loads((FIXTURES / "advice_prompts.json").read_text(encoding="utf-8"))["groups"]
    return [p for g in groups for p in g["prompts"]]


def _pii_samples() -> list[dict]:
    return json.loads((FIXTURES / "pii_samples.json").read_text(encoding="utf-8"))["samples"]


def _types(result: ComplianceResult) -> set[str]:
    return {f.type for f in result.flags}


# ---------------------------------------------------------------------------
# Inbound: advice and return requests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prompt", _advice_prompts())
def test_every_advice_fixture_is_flagged_inbound(agent, prompt):
    result = agent.inbound(prompt)
    assert result.status == "ok"
    assert "advice_request" in _types(result), prompt
    assert result.clean is False
    assert is_advice_request(result)


@pytest.mark.parametrize(
    "prompt",
    [
        "Should I sell my small cap units? Be honest.",
        "what do you suggest, redeem or hold my silver fund?",
        "Which fund is better for me, large cap or mid cap?",
        "Do you recommend the gold fund?",
        "How much return will the mid cap fund give next year?",
        "Will my small cap investment double in 3 years?",
        "Tell me whether I should continue my SIP.",
        "Is the silver fund safe to invest in?",
        "mujhe kya karna chahiye, gold fund bech dun ya rakhun?",
        "small cap fund me kitna return milega?",
    ],
)
def test_rephrased_advice_questions_are_flagged(agent, prompt):
    assert "advice_request" in _types(agent.inbound(prompt)), prompt


@pytest.mark.parametrize(
    "text",
    [
        "What is the exit load if I redeem within a year?",
        "Is the exit load 1% right now?",
        "I got 20% returns last year but my withdrawal failed.",
        "How are returns taxed?",
        "My withdrawal has been stuck for 5 days.",
        "The app crashes whenever I open the SIP page.",
        "Is this app good? It keeps logging me out.",
        "Where should I go to update my bank details?",
        "Need OTP chahiye but it never arrives.",
        "A friend suggested this app, but KYC is pending.",
        "Great app, best investment platform I have used.",
        "What is the minimum SIP amount?",
        "Who is the fund manager of the large cap fund?",
    ],
)
def test_ordinary_complaints_and_facts_questions_are_not_advice(agent, text):
    result = agent.inbound(text)
    assert "advice_request" not in _types(result), text


def test_real_reviews_are_rarely_flagged(agent, data):
    reviews = [r.text for r in data.themes.reviews.values()]
    flagged = sum(1 for text in reviews if not agent.inbound(text).clean)
    assert len(reviews) > 1000
    assert flagged / len(reviews) < 0.02, f"{flagged} of {len(reviews)} real reviews flagged"


# ---------------------------------------------------------------------------
# Outbound: advice and performance claims in a draft
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "draft",
    [
        "You may want to stay invested longer.",
        "You should sell the mid cap fund before the end of the month.",
        "We recommend switching to the large cap fund.",
        "I would suggest that you hold on to your units.",
        "It is a good fund, so keep investing.",
        "This is a great scheme for long-term investors.",
        "Consider increasing your SIP amount.",
        "Now is the right time to buy.",
    ],
)
def test_advice_in_a_draft_is_flagged(agent, draft):
    result = agent.outbound(draft)
    assert result.mode == "outbound"
    assert "advice_in_text" in _types(result), draft
    assert result.clean is False


@pytest.mark.parametrize(
    "draft",
    [
        "The fund will give 15% returns next year.",
        "It has delivered high returns over the last 3 years.",
        "Expected returns are around 12% a year.",
        "It is guaranteed to beat the market.",
        "The fund has outperformed its benchmark.",
        "You can earn 10% annually from this scheme.",
    ],
)
def test_performance_claims_in_a_draft_are_flagged(agent, draft):
    assert "performance_claim" in _types(agent.outbound(draft)), draft


@pytest.mark.parametrize(
    "draft",
    [
        "If you redeem within one year, returns are taxed at 20%.",
        "Returns are taxed at 20% if you redeem within a year.",
        "Gains above Rs 1.25 lakh are taxed at 12.5% after one year.",
        "The exit load is 1% if you redeem within 1 year.",
        "The expense ratio is 0.75%.",
        "Stamp duty on investment is 0.005%.",
        "We cannot guarantee returns, and I cannot say whether to buy or sell.",
        "I cannot tell you whether to redeem, hold or buy more. That is your decision.",
        "I can only share facts about the scheme, not advice on whether to sell.",
    ],
)
def test_tax_fee_and_refusal_sentences_are_not_flagged(agent, draft):
    result = agent.outbound(draft)
    assert result.clean, (draft, [(f.type, f.detail) for f in result.flags])


def test_every_corpus_tax_sentence_passes_outbound(agent, data):
    sentences = []
    for scheme in data.corpus.schemes.values():
        block = scheme.sections.get("Stamp duty and tax", "")
        assert block, scheme.scheme_id
        sentences.append(block)
    for block in sentences:
        assert agent.outbound(block).clean, block


def test_no_corpus_block_is_flagged_outbound(agent, data):
    for scheme in data.corpus.schemes.values():
        for block in scheme.text_blocks:
            if "management" in block.section_title.lower():
                continue
            result = agent.outbound(block.text)
            assert result.clean, (scheme.scheme_id, block.section_title, result.flags)


def test_the_tax_whitelist_is_built_from_the_corpus(data):
    whitelist = tax_sentences(data.corpus)
    assert whitelist
    assert any("taxed at 20" in s for s in whitelist)


def test_the_whitelist_does_not_excuse_a_return_claim_in_another_sentence(agent):
    draft = "Returns are taxed at 20%. The fund will give 15% returns next year."
    assert "performance_claim" in _types(agent.outbound(draft))


def test_a_percentage_without_a_return_word_is_fine(agent):
    assert agent.outbound("The fund holds 12% of its assets in cash.").clean


def test_a_normal_answer_with_a_link_and_date_is_clean(agent, data):
    scheme = data.corpus.schemes["mid_cap"]
    draft = (
        "The exit load is 1% if you redeem within one year. "
        f"Source: {scheme.source_url}. Last checked: {scheme.fetched_at[:10]}."
    )
    assert agent.outbound(draft).clean


# ---------------------------------------------------------------------------
# PII
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sample", _pii_samples(), ids=lambda s: s["id"])
@pytest.mark.parametrize("mode", ["inbound", "outbound"])
def test_residual_pii_is_flagged_in_both_modes(agent, sample, mode):
    result = agent.run(sample["text"], mode=mode)
    assert "pii_residual" in _types(result), sample["id"]
    assert has_pii(result)
    if sample["type"] != "MULTI":
        assert f"pii.{sample['type'].lower()}" in {f.detail for f in result.flags}
        assert getattr(result.pii_redacted, sample["type"]) >= 1
    else:
        assert {"pii.pan", "pii.phone", "pii.email"} <= {f.detail for f in result.flags}


@pytest.mark.parametrize(
    "draft",
    [
        "Your PAN ABCDE1234F is verified.",
        "We will call you on 9876543210.",
        "We have written to test.user@example.com.",
    ],
)
def test_a_draft_with_pii_is_flagged(agent, draft):
    assert "pii_residual" in _types(agent.outbound(draft))


def test_redacted_placeholders_are_not_pii(agent):
    result = agent.inbound("My PAN is [PAN] and my phone is [PHONE], the withdrawal is stuck.")
    assert "pii_residual" not in _types(result)
    assert result.clean


def test_a_pii_flag_alone_is_not_an_advice_attempt(agent):
    result = agent.inbound("Call me on 9876543210, my withdrawal is stuck.")
    assert has_pii(result)
    assert not is_advice_request(result)


def test_pii_and_advice_together_give_both_flags(agent):
    result = agent.inbound("My PAN is ABCDE1234F. Should I sell my mid cap fund?")
    assert _types(result) == {"advice_request", "pii_residual"}
    assert set(hard_fail_types(result)) == {"advice_request", "pii_residual"}


# ---------------------------------------------------------------------------
# Result shape and privacy
# ---------------------------------------------------------------------------


def test_flag_details_never_contain_the_matched_text(agent):
    texts = [
        ("My PAN is ABCDE1234F and my email is test.user@example.com, call 9876543210.", "inbound"),
        ("Should I redeem my HDFC Mid Cap units now?", "inbound"),
        ("You may want to stay invested longer. The fund will give 15% returns.", "outbound"),
        ("Your PAN ABCDE1234F is verified. We recommend you sell.", "outbound"),
    ]
    for text, mode in texts:
        result = agent.run(text, mode=mode)
        assert result.flags
        dump = result.model_dump_json().lower()
        for secret in ("abcde1234f", "test.user", "9876543210", "redeem my hdfc", "stay invested", "15%"):
            assert secret not in dump, (secret, dump)
        for flag in result.flags:
            assert flag.detail.startswith(("rule.", "pii.", "classifier."))


def test_clean_text_gives_an_ok_clean_result(agent):
    result = agent.inbound("My withdrawal has been pending for three days.")
    assert (result.status, result.clean, result.flags, result.error) == ("ok", True, (), None)
    assert result.mode == "inbound"


def test_flags_are_not_repeated(agent):
    result = agent.inbound("Should I sell? Should I sell? Should I sell my mid cap fund?")
    assert len(result.flags) == len(set(result.flags))


def test_the_result_has_only_contract_fields(agent):
    assert set(json.loads(agent.inbound("hello").model_dump_json())) == set(ComplianceResult.model_fields)


def test_the_agent_is_stateless(agent):
    first = agent.inbound("Should I sell my mid cap fund?")
    agent.inbound("My withdrawal is stuck.")
    assert agent.inbound("Should I sell my mid cap fund?") == first


def test_the_input_is_not_changed_and_long_text_is_handled(agent):
    text = "withdrawal stuck " * 400 + "Should I sell my mid cap fund?"
    assert "advice_request" in _types(agent.inbound(text))


def test_run_compliance_wrapper_uses_default_rules():
    assert not run_compliance("Should I sell my mid cap fund?").clean
    assert run_compliance("You may want to stay invested.", mode="outbound").flags


# ---------------------------------------------------------------------------
# Errors and the optional model layer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "   ", None, 42, b"bytes"])
def test_bad_input_is_an_error_result_and_never_clean(agent, bad):
    result = agent.run(bad)  # type: ignore[arg-type]
    assert (result.status, result.clean, result.error) == ("error", False, "COMPLIANCE_BAD_INPUT")


def test_bad_mode_is_an_error_result(agent):
    result = agent.run("hello", mode="sideways")
    assert (result.status, result.clean, result.error) == ("error", False, "COMPLIANCE_BAD_MODE")


class _Classifier:
    def __init__(self, label=None, exc=None):
        self.label, self.exc, self.calls = label, exc, []

    def classify(self, text, *, mode):
        self.calls.append(mode)
        if self.exc:
            raise self.exc
        return self.label


def test_the_model_can_add_a_flag_the_rules_miss(data):
    classifier = _Classifier("advice_request")
    result = ComplianceAgent(corpus=data.corpus, classifier=classifier).inbound("Thoughts on parking money here?")
    assert [(f.type, f.detail) for f in result.flags] == [("advice_request", "classifier.advice")]
    assert classifier.calls == ["inbound"]


def test_the_model_cannot_clear_a_rule_flag(data):
    agent = ComplianceAgent(corpus=data.corpus, classifier=_Classifier("none"))
    assert not agent.inbound("Should I redeem my HDFC Mid Cap units now?").clean


@pytest.mark.parametrize("label,expected", [("advice_in_text", "advice_in_text"), ("performance_claim", "performance_claim")])
def test_the_outbound_model_label_maps_to_a_flag_type(data, label, expected):
    agent = ComplianceAgent(corpus=data.corpus, classifier=_Classifier(label))
    assert _types(agent.outbound("This is a draft reply.")) == {expected}


def test_a_failing_model_gives_an_error_result_not_a_clean_one(data):
    agent = ComplianceAgent(corpus=data.corpus, classifier=_Classifier(exc=RuntimeError("boom secret")))
    result = agent.inbound("My withdrawal is stuck.")
    assert (result.status, result.clean, result.error) == ("error", False, "COMPLIANCE_CLASSIFIER_FAILED")
    assert "secret" not in result.model_dump_json()


def test_an_invalid_model_label_fails_closed(data):
    agent = ComplianceAgent(corpus=data.corpus, classifier=_Classifier("maybe"))
    result = agent.outbound("This is a draft reply.")
    assert (result.status, result.clean, result.error) == ("error", False, "COMPLIANCE_CLASSIFIER_FAILED")


# ---------------------------------------------------------------------------
# Rule file and structure
# ---------------------------------------------------------------------------


def test_the_rule_file_loads_with_both_directions():
    rules = load_rules()
    assert rules.inbound and rules.outbound
    assert {r.flag_type for r in rules.inbound} == {"advice_request"}
    assert {r.flag_type for r in rules.outbound} == {"advice_in_text", "performance_claim"}
    for direction in (rules.inbound, rules.outbound):
        codes = [r.code for r in direction]
        assert len(codes) == len(set(codes))


def test_compliance_does_not_import_the_other_agents():
    tree = ast.parse((SRC / "agents" / "compliance.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = {"agents.facts", "agents.pattern", "agents.stubs"}
    assert not (imported & forbidden), imported & forbidden


def test_compliance_never_writes_logs_or_touches_the_network():
    source = (SRC / "agents" / "compliance.py").read_text(encoding="utf-8")
    for banned in ("write_text", "write_bytes", "requests", "urllib", "socket", "print(", "logging"):
        assert banned not in source, banned
