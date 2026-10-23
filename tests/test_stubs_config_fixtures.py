"""Stub agents, settings and the fixtures folder."""

import json
from pathlib import Path

import pytest

from agents.stubs import stub_compliance, stub_facts, stub_pattern
from config import LLM_USES, ModelSettings, Settings, load_settings
from constants import (
    ALLOWED_CITATIONS,
    MAX_REFINEMENT_ROUNDS,
    MAX_REPLY_SENTENCES,
    MAX_REVIEWER_REWORK_ROUNDS,
    PII_TYPES,
)
from paths import DATA_DIR
from schemas import ComplianceResult, FactsResult, PatternResult

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", ["covered", "not_covered", "ambiguous", "error"])
def test_facts_stub_returns_a_valid_result_for_every_scenario(scenario):
    result = stub_facts("any complaint", scenario=scenario)
    assert isinstance(result, FactsResult)
    assert result.status == scenario
    if scenario == "covered":
        assert result.citation in ALLOWED_CITATIONS


@pytest.mark.parametrize("scenario", ["one_off", "recurring", "rising", "error"])
def test_pattern_stub_returns_a_valid_result_for_every_scenario(scenario):
    result = stub_pattern("any complaint", scenario=scenario)
    assert isinstance(result, PatternResult)
    assert result.status == ("error" if scenario == "error" else "ok")
    if scenario != "error":
        assert result.signal == scenario


@pytest.mark.parametrize("scenario", ["clean", "advice_request", "error"])
@pytest.mark.parametrize("mode", ["inbound", "outbound"])
def test_compliance_stub_returns_a_valid_result_for_every_scenario(scenario, mode):
    result = stub_compliance("any complaint", scenario=scenario, mode=mode)
    assert isinstance(result, ComplianceResult)
    assert result.mode == mode
    assert result.clean == (scenario == "clean")


def test_stubs_ignore_the_complaint_text():
    assert stub_facts("one") == stub_facts("two")
    assert stub_pattern("one") == stub_pattern("two")
    assert stub_compliance("one") == stub_compliance("two")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_settings_load_with_the_documented_defaults():
    settings = load_settings()
    assert settings.pattern.min_count == 5
    assert settings.pattern.rising_ratio == 1.5
    assert settings.pattern.window_days == 7
    assert settings.ui.form == "cli"
    assert settings.data_path == DATA_DIR


def test_caps_in_settings_match_the_problem_statement():
    limits = load_settings().limits
    assert limits.reviewer_rework_rounds == MAX_REVIEWER_REWORK_ROUNDS == 1
    assert limits.refinement_rounds == MAX_REFINEMENT_ROUNDS == 5
    assert limits.max_reply_sentences == MAX_REPLY_SENTENCES == 3


def test_open_decisions_are_listed_and_cannot_be_used_by_accident():
    # An empty Settings() is "nothing decided yet", whatever config/settings.toml says today.
    settings = Settings()
    assert settings.unresolved() == [
        "llm.facts.provider",
        "llm.facts.model",
        "llm.pattern.provider",
        "llm.pattern.model",
        "llm.compliance.provider",
        "llm.compliance.model",
        "llm.drafter.provider",
        "llm.drafter.model",
        "llm.reviewer.provider",
        "llm.reviewer.model",
        "llm.refinement.provider",
        "llm.refinement.model",
        "mcp.google_doc_id",
        "mcp.email_recipient",
    ]
    with pytest.raises(RuntimeError, match="llm.facts.model"):
        settings.require("llm.facts.model")
    settings.require()  # nothing requested, nothing missing


def test_the_default_model_fills_in_any_use_that_leaves_its_own_empty():
    settings = Settings(llm={"provider": "groq", "model": "m"})
    assert settings.unresolved() == ["mcp.google_doc_id", "mcp.email_recipient"]
    assert settings.llm.for_use("reviewer").provider == "groq"


def test_the_reviewer_has_its_own_model_entry_and_the_project_uses_rules_for_it():
    assert "reviewer" in LLM_USES
    chosen = load_settings().llm.for_use("reviewer")
    assert chosen.provider == "none"


def _settings_from(tmp_path, text):
    path = tmp_path / "settings.toml"
    path.write_text(text, encoding="utf-8")
    return load_settings(path)


def test_each_model_use_has_its_own_entry():
    settings = load_settings()
    assert LLM_USES == ("facts", "pattern", "compliance", "drafter", "reviewer", "refinement")
    for use in LLM_USES:
        assert isinstance(getattr(settings.llm, use), ModelSettings)


def test_the_default_model_fills_every_use_that_has_no_entry(tmp_path):
    settings = _settings_from(tmp_path, '[llm]\nprovider = "p"\nmodel = "m"\n')
    assert not [name for name in settings.unresolved() if name.startswith("llm.")]
    for use in LLM_USES:
        assert (settings.llm.for_use(use).provider, settings.llm.for_use(use).model) == ("p", "m")


def test_a_use_can_have_a_different_model_from_the_others(tmp_path):
    settings = _settings_from(
        tmp_path,
        '[llm]\nprovider = "p"\nmodel = "m"\n[llm.compliance]\nmodel = "strict"\n',
    )
    assert settings.llm.for_use("compliance").model == "strict"
    assert settings.llm.for_use("compliance").provider == "p"
    assert settings.llm.for_use("facts").model == "m"


def test_only_the_unset_uses_are_listed_as_open(tmp_path):
    text = '[llm.facts]\nprovider = "p"\nmodel = "m"\n[llm.pattern]\nprovider = "p"\nmodel = "m"\n'
    open_llm = [n for n in _settings_from(tmp_path, text).unresolved() if n.startswith("llm.")]
    assert open_llm == [
        "llm.compliance.provider",
        "llm.compliance.model",
        "llm.drafter.provider",
        "llm.drafter.model",
        "llm.reviewer.provider",
        "llm.reviewer.model",
        "llm.refinement.provider",
        "llm.refinement.model",
    ]


def test_the_refinement_classifier_has_its_own_model_entry_and_the_project_uses_gemini_for_it():
    assert "refinement" in LLM_USES
    chosen = load_settings().llm.for_use("refinement")
    assert chosen.provider == "gemini"
    assert chosen.model.startswith("gemini-")


def test_an_unknown_key_in_a_model_entry_is_rejected(tmp_path):
    with pytest.raises(Exception):
        _settings_from(tmp_path, '[llm.facts]\napi_key = "x"\n')


def test_settings_reject_unknown_keys(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('data_dir = "data"\n[llm]\napi_key = "x"\n', encoding="utf-8")
    with pytest.raises(Exception):
        load_settings(path)


# ---------------------------------------------------------------------------
# Fixtures folder
# ---------------------------------------------------------------------------


def test_pii_fixtures_are_labelled_synthetic_and_consistent():
    content = json.loads((FIXTURES / "pii_samples.json").read_text(encoding="utf-8"))
    assert "SYNTHETIC" in content["_note"]
    samples = content["samples"]
    assert {s["type"] for s in samples} >= set(PII_TYPES)
    ids = [s["id"] for s in samples]
    assert len(ids) == len(set(ids))
    for sample in samples:
        assert sample["raw_values"], sample["id"]
        assert any(value in sample["text"] for value in sample["raw_values"]), sample["id"]


def test_pii_fixture_values_do_not_occur_in_the_real_reviews(data):
    content = json.loads((FIXTURES / "pii_samples.json").read_text(encoding="utf-8"))
    values = {v for s in content["samples"] for v in s["raw_values"]}
    haystack = "\n".join(review.text for review in data.themes.reviews.values())
    assert [v for v in values if v in haystack] == []


def test_advice_prompt_fixtures_cover_the_three_required_intents():
    content = json.loads((FIXTURES / "advice_prompts.json").read_text(encoding="utf-8"))
    intents = {group["intent"]: group["prompts"] for group in content["groups"]}
    assert {"should_i_redeem", "is_scheme_good", "expected_returns"} <= set(intents)
    assert all(len(prompts) >= 2 for prompts in intents.values())
    all_prompts = [p for prompts in intents.values() for p in prompts]
    assert len(all_prompts) == len(set(all_prompts))
