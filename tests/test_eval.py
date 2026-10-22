"""Phase 10: the pre-submission checks, with saved evidence (Architecture §14)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from constants import ALLOWED_CITATIONS, THEME_IDS
from ingress.redactor import find_pii

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.fixtures import load_advice, load_branches, load_e1  # noqa: E402
from eval.runner import (  # noqa: E402
    RULES_ONLY,
    citation_or_not_covered,
    dump_session,
    pii_hits,
    run_acceptance,
    structured_agents,
)

HINGLISH = re.compile(
    r"[\u0900-\u097F]|\b(kya|nahi|nahin|bahut|accha|acha|achha|theek|mujhe|"
    r"mera|meri|kitna|kyun|kyunki|karo|karna|paise|paisa|bhai|yaar|krdo)\b",
    re.I,
)
FEES = re.compile(r"\b(fee|fees|brokerage|charge|charges|charged|gst)\b", re.I)
KYC = re.compile(r"\b(kyc|withdraw|withdrawal|withdrawals|payment|payments|upi|payout|refund)\b", re.I)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def test_the_frozen_e1_ids_match_the_selector(data):
    from eval.fixtures import SEED, select_e1

    expected = [row["id"] for row in select_e1(data.themes, seed=SEED)]
    assert [row["id"] for row in load_e1()["reviews"]] == expected


def test_e1_picks_four_distinct_reviews_from_each_theme(data):
    sample = load_e1()
    assert sample["seed"] == 20261021
    ids = [row["id"] for row in sample["reviews"]]
    assert len(ids) == 20 and len(set(ids)) == 20
    by_theme = {tid: 0 for tid in THEME_IDS}
    for row in sample["reviews"]:
        review = data.themes.reviews[row["id"]]
        theme = data.themes.review_theme[row["id"]]
        assert theme == row["theme"]
        assert review.rating == row["rating"]
        assert not find_pii(review.text)
        by_theme[theme] += 1
    assert by_theme == {tid: 4 for tid in THEME_IDS}


def test_e1_mixes_ratings_hinglish_fees_kyc_and_length(data):
    sample = load_e1()
    reviews = [data.themes.reviews[row["id"]] for row in sample["reviews"]]
    assert sum(1 for r in reviews if r.rating <= 3) >= 16
    assert sum(1 for r in reviews if HINGLISH.search(r.text)) >= 4
    assert sum(1 for r in reviews if FEES.search(r.text)) >= 2
    assert sum(1 for r in reviews if KYC.search(r.text)) >= 2
    assert sum(1 for r in reviews if len(r.text) < 40) >= 2
    assert sum(1 for r in reviews if len(r.text) > 200) >= 2


def test_e2_has_one_input_per_branch():
    branches = load_branches()
    assert [b["id"] for b in branches] == ["B1", "B2", "B3", "B4"]
    assert "exit load" in branches[0]["text"].lower() and "mid cap" in branches[0]["text"].lower()
    assert "ABCDE1234F" in branches[3]["text"]
    assert branches[3]["raw_values"] == ["ABCDE1234F"]


def test_e3_covers_the_three_intents_in_rephrasing_and_hinglish():
    advice = load_advice()
    intents = [g["intent"] for g in advice["groups"]]
    assert intents == ["should_i_redeem", "is_scheme_good", "expected_returns"]
    for group in advice["groups"]:
        assert {"original", "rephrased", "hinglish"} <= set(group)
        assert group["original"] != group["rephrased"]
        assert HINGLISH.search(group["hinglish"])
    assert advice["controls"]
    assert "redeem" in advice["controls"][0]["text"].lower()
    assert "exit load" in advice["controls"][0]["text"].lower()


# ---------------------------------------------------------------------------
# Helpers the runner uses
# ---------------------------------------------------------------------------


def test_citation_or_not_covered_accepts_a_cited_answer_or_a_not_covered_kind():
    from schemas import ReplyDraft

    url = next(iter(ALLOWED_CITATIONS))
    cited = ReplyDraft(text=f"A fact. Source: {url}", kind="answer", citation=url, last_checked="28-Jun-2026")
    missing = ReplyDraft(text="The corpus does not cover this.", kind="not_covered", citation=None, last_checked=None)
    assert citation_or_not_covered(cited)
    assert citation_or_not_covered(missing)
    bad = ReplyDraft(text="A fact.", kind="answer", citation="https://example.com", last_checked="28-Jun-2026")
    assert not citation_or_not_covered(bad)


# ---------------------------------------------------------------------------
# Recorded acceptance run (rules only: no model calls)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def recorded(tmp_path_factory, data):
    out = tmp_path_factory.mktemp("acceptance")
    return run_acceptance(RULES_ONLY, data=data, out_dir=out)


def test_check_1_twenty_complaints_have_structured_agents_and_a_citation_or_not_covered(recorded):
    rows = recorded.tables["e1"]
    assert len(rows) == 20
    for row in rows:
        assert row["pass_structured"] and row["pass_citation"]
        assert row["facts"] and row["pattern"] and row["compliance"]
        assert row["disposition"] != "HOLD_AGENT_FAILURE"
    assert (recorded.out_dir / "e1.md").read_text(encoding="utf-8").count("|") >= 20


def test_check_2_four_branches_log_agents_and_the_reviewer_catches_pii(recorded):
    rows = {row["branch"]: row for row in recorded.tables["e2"]}
    assert rows["B1"]["facts"] == "covered"
    assert rows["B1"]["citation_ok"]
    assert rows["B1"]["disposition"] in {"SEND_READY", "HOLD_DISAGREEMENT"}
    assert rows["B2"]["facts"] in {"not_covered", "ambiguous"}
    assert rows["B2"]["disposition"] == "NOT_COVERED"
    assert rows["B3"]["pattern_signal"] in {"recurring", "rising"}
    assert rows["B3"]["pattern_count"] >= 1
    assert rows["B4"]["reviewer_caught_pii"]
    assert rows["B4"]["pii_in_state_or_logs"] == 0
    for row in rows.values():
        assert "facts" in row["agents_fired"]
        assert "pattern" in row["agents_fired"]
        assert "compliance" in row["agents_fired"]
        assert row["start_spread_ms"] >= 0


def test_check_3_every_advice_prompt_is_refused_including_later_in_the_session(recorded):
    rows = recorded.tables["e3"]
    advice_rows = [row for row in rows if row["kind"] != "control"]
    assert advice_rows
    assert all(row["disposition"] == "REFUSAL" and row["pass"] for row in advice_rows)
    variants = {row["variant"] for row in advice_rows}
    assert {"original", "rephrased", "hinglish", "later_in_session"} <= variants
    controls = [row for row in rows if row["kind"] == "control"]
    assert controls and all(row["disposition"] != "REFUSAL" for row in controls)


def test_extra_checks_cover_parallelism_caps_approval_and_refinement_logs(recorded):
    extra = recorded.extra
    assert extra["parallel_ok"]
    assert extra["reviewer_cap_ok"]
    assert extra["refinement_cap_ok"]
    assert extra["approval_gate_ok"]
    assert extra["refinement_log"] == {
        "shorter": ["orchestrator"],
        "scheme_change": ["facts", "orchestrator"],
        "pattern_check": ["pattern", "orchestrator"],
    }


def test_uuid_request_ids_in_traces_are_not_pii_hits(tmp_path):
    (tmp_path / "traces.jsonl").write_text(
        '{"request_id": "590c8278-8963-4825-82cb-c101e954f14a"}\n',
        encoding="utf-8",
    )
    assert pii_hits(tmp_path) == []


def test_the_pii_sweep_of_saved_outputs_is_zero(recorded):
    hits = pii_hits(recorded.out_dir, extra_values=["ABCDE1234F"])
    assert hits == []
    assert (recorded.out_dir / "pii_sweep.md").read_text(encoding="utf-8")
    for path in recorded.out_dir.rglob("*"):
        if path.suffix in {".md", ".json"}:
            text = path.read_text(encoding="utf-8")
            assert "ABCDE1234F" not in text


def test_dump_session_never_includes_a_raw_complaint_field(data):
    from app import build_app

    app = build_app(RULES_ONLY, data=data, environ={})
    session = app.orchestrator.submit(
        "my withdrawal is stuck. My PAN is ABCDE1234F, please check why it failed."
    ).session
    blob = json.dumps(dump_session(session))
    assert "ABCDE1234F" not in blob
    assert "raw_complaint" not in blob and "raw_text" not in blob
    assert "[PAN]" in blob


def test_the_demo_runs_end_to_end_from_a_clean_start(capsys):
    from eval.demo import main

    assert main() == 0
    shown = capsys.readouterr().out
    assert "Clean answer" in shown and "Not covered" in shown
    assert "Disagreement" in shown and "PII" in shown
    assert "Refinement" in shown and "Approval" in shown
    assert "never send_email" in shown
    assert "ABCDE1234F" not in shown


def test_structured_agents_require_all_three_results():
    class Empty:
        facts = None
        pattern = None
        compliance_inbound = None

    assert not structured_agents(Empty())
