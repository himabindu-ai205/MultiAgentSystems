"""Phase 5: the decision table (Architecture section 8.1)."""

from __future__ import annotations

import ast
import itertools
from pathlib import Path

import pytest

from agents.stubs import stub_compliance, stub_facts, stub_pattern
from constants import DISPOSITION_VALUES
from orchestrator.disposition import decide, failed_agents, is_advice_request
from schemas import ComplianceResult, Flag

SRC = Path(__file__).resolve().parents[1] / "src"

FACTS = ("covered", "not_covered", "ambiguous", "error")
PATTERN = ("one_off", "recurring", "rising", "error")
COMPLIANCE = ("clean", "advice_request", "error")


def _expected(facts: str, pattern: str, compliance: str) -> str:
    """The table written out again, independently of the code under test."""
    if "error" in (facts, pattern, compliance):
        return "HOLD_AGENT_FAILURE"
    if compliance == "advice_request":
        return "REFUSAL"
    if facts == "covered":
        return "HOLD_DISAGREEMENT" if pattern == "rising" else "SEND_READY"
    return "NOT_COVERED"


@pytest.mark.parametrize("facts,pattern,compliance", list(itertools.product(FACTS, PATTERN, COMPLIANCE)))
def test_every_combination_matches_the_decision_table(facts, pattern, compliance):
    result = decide(
        stub_facts(scenario=facts),
        stub_pattern(scenario=pattern),
        stub_compliance(scenario=compliance),
    )
    assert result.value == _expected(facts, pattern, compliance)
    assert result.value in DISPOSITION_VALUES


def test_facts_covered_and_pattern_rising_is_a_disagreement():
    result = decide(stub_facts(), stub_pattern(scenario="rising"), stub_compliance())
    assert result.value == "HOLD_DISAGREEMENT"


@pytest.mark.parametrize("pattern", ["one_off", "recurring"])
def test_facts_covered_and_pattern_not_rising_is_send_ready(pattern):
    assert decide(stub_facts(), stub_pattern(scenario=pattern), stub_compliance()).value == "SEND_READY"


def test_ambiguous_facts_is_not_covered():
    assert decide(stub_facts(scenario="ambiguous"), stub_pattern(scenario="rising"), stub_compliance()).value == "NOT_COVERED"


@pytest.mark.parametrize("pattern", ["one_off", "recurring", "rising"])
def test_advice_wins_whatever_pattern_says(pattern):
    result = decide(stub_facts(), stub_pattern(scenario=pattern), stub_compliance(scenario="advice_request"))
    assert result.value == "REFUSAL"


@pytest.mark.parametrize("which", ["facts", "pattern", "compliance"])
def test_one_failed_agent_ends_in_hold_agent_failure(which):
    results = {
        "facts": stub_facts(),
        "pattern": stub_pattern(),
        "compliance": stub_compliance(),
    }
    results[which] = {
        "facts": stub_facts(scenario="error"),
        "pattern": stub_pattern(scenario="error"),
        "compliance": stub_compliance(scenario="error"),
    }[which]
    assert decide(**results).value == "HOLD_AGENT_FAILURE"
    assert [f.agent for f in failed_agents(**results)] == [which]


def test_all_failed_agents_are_named():
    failures = failed_agents(
        stub_facts(scenario="error"), stub_pattern(scenario="error"), stub_compliance(scenario="error")
    )
    assert [f.agent for f in failures] == ["facts", "pattern", "compliance"]
    assert all(f.code.isupper() for f in failures)


def test_a_failure_beats_an_advice_request():
    result = decide(stub_facts(scenario="error"), stub_pattern(), stub_compliance(scenario="advice_request"))
    assert result.value == "HOLD_AGENT_FAILURE"


@pytest.mark.parametrize("missing", ["facts", "pattern", "compliance"])
def test_a_missing_result_counts_as_a_failure(missing):
    results = {"facts": stub_facts(), "pattern": stub_pattern(), "compliance": stub_compliance()}
    results[missing] = None
    assert decide(**results).value == "HOLD_AGENT_FAILURE"
    assert failed_agents(**results)[0].code == "AGENT_MISSING"


def test_an_outbound_compliance_result_is_not_accepted():
    outbound = stub_compliance(mode="outbound")
    assert decide(stub_facts(), stub_pattern(), outbound).value == "HOLD_AGENT_FAILURE"
    assert failed_agents(stub_facts(), stub_pattern(), outbound)[0].code == "COMPLIANCE_WRONG_MODE"


def test_residual_pii_alone_does_not_change_the_disposition():
    pii_only = ComplianceResult(
        mode="inbound", status="ok", clean=False, flags=(Flag(type="pii_residual", detail="pii.pan"),)
    )
    assert not is_advice_request(pii_only)
    assert decide(stub_facts(), stub_pattern(), pii_only).value == "SEND_READY"


def test_an_unexpected_flag_type_leans_towards_refusal():
    odd = ComplianceResult(
        mode="inbound", status="ok", clean=False, flags=(Flag(type="performance_claim", detail="rule.x"),)
    )
    assert decide(stub_facts(), stub_pattern(), odd).value == "REFUSAL"


def test_pii_and_advice_together_is_a_refusal():
    both = ComplianceResult(
        mode="inbound",
        status="ok",
        clean=False,
        flags=(Flag(type="advice_request", detail="rule.x"), Flag(type="pii_residual", detail="pii.pan")),
    )
    assert decide(stub_facts(), stub_pattern(), both).value == "REFUSAL"


def test_the_same_results_always_give_the_same_disposition():
    args = (stub_facts(), stub_pattern(scenario="rising"), stub_compliance())
    assert len({decide(*args).value for _ in range(20)}) == 1


def test_disposition_module_has_no_model_or_agent_imports():
    tree = ast.parse((SRC / "orchestrator" / "disposition.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported <= {"__future__", "dataclasses", "schemas"}, imported
