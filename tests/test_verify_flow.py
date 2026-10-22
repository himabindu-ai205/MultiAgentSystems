"""The manual walkthrough script prints the whole ticket without leaking raw PII."""

from __future__ import annotations

from config import Settings
from constants import SCHEME_SOURCE_URLS
from orchestrator.report import EXIT_LOAD_NAMED, EXIT_LOAD_QUESTION, format_run

from test_app import stub_app


def test_the_default_question_is_the_exit_load_complaint():
    assert "exit load" in EXIT_LOAD_QUESTION.lower()
    assert "amount" in EXIT_LOAD_QUESTION.lower()
    assert "HDFC Large Cap" in EXIT_LOAD_NAMED


def test_the_report_shows_every_stage_of_a_run():
    app = stub_app()
    run = app.orchestrator.submit(EXIT_LOAD_NAMED)
    text = format_run(run)

    assert run.session.request_id in text
    assert "Facts" in text and "Pattern" in text and "Compliance" in text
    assert "Disposition" in text and "Reviewer" in text
    assert "spread" in text.lower()
    assert SCHEME_SOURCE_URLS["large_cap"] in text or "ambiguous" in text.lower()
    assert "Agents fired" in text


def test_the_report_never_prints_a_raw_pan():
    pan = "ABCDE1234F"
    app = stub_app()
    run = app.orchestrator.submit(f"my PAN is {pan}, {EXIT_LOAD_NAMED}")
    text = format_run(run)

    assert pan not in text
    assert "[PAN]" in text


def test_the_real_stack_answers_a_named_exit_load_question(data):
    from app import build_app

    settings = Settings(llm={"provider": "none", "reviewer": {"provider": "none"}, "refinement": {"provider": "none"}})
    run = build_app(settings, data=data).orchestrator.submit(EXIT_LOAD_NAMED)
    text = format_run(run)

    assert run.session.results.facts.status == "covered"
    assert run.session.results.facts.section_title == "Exit load"
    assert "1%" in (run.session.results.facts.answer or "")
    assert run.review is not None and run.review.verdict == "PASS"
    assert "covered" in text and "PASS" in text


def test_without_a_scheme_facts_asks_which_fund(data):
    from app import build_app

    settings = Settings(llm={"provider": "none", "reviewer": {"provider": "none"}, "refinement": {"provider": "none"}})
    run = build_app(settings, data=data).orchestrator.submit(EXIT_LOAD_QUESTION)
    assert run.session.results.facts.status == "ambiguous"
    assert "ambiguous" in format_run(run).lower()
