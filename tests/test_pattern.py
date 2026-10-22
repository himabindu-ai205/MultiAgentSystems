"""Phase 3: Pattern Agent (Architecture section 6.2)."""

import ast
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from agents import pattern as pattern_module
from agents.pattern import PatternAgent, load_vocabulary, stem, tokens
from config import PatternSettings, load_settings
from constants import THEME_IDS
from data_layer.themes import Review, Themes
from schemas import PatternResult

SRC = Path(pattern_module.__file__).resolve().parents[1]
ANCHOR = date(2026, 7, 4)


@pytest.fixture(scope="module")
def agent(data):
    return PatternAgent(data.themes)


# ---------------------------------------------------------------------------
# A small synthetic data set, for exact signal and window tests
# ---------------------------------------------------------------------------


def make_themes(rows) -> Themes:
    """rows: (days_before_anchor, text, theme_id). The newest row must be 0 days back."""
    reviews, review_theme = {}, {}
    for i, (days_back, text, theme_id) in enumerate(rows):
        review_id = hashlib.sha256(f"{i}-{text}".encode()).hexdigest()[:16]
        when = datetime(ANCHOR.year, ANCHOR.month, ANCHOR.day, 12, tzinfo=timezone.utc) - timedelta(days=days_back)
        reviews[review_id] = Review(review_id, text, 1, when, None)
        review_theme[review_id] = theme_id
    return Themes(
        themes={}, top_theme_ids=(), reviews=reviews, review_theme=review_theme, anchor_date=ANCHOR
    )


def withdrawals(counts_by_week, text="withdrawal stuck money not received"):
    """counts_by_week[k] reviews in the k-th 7-day window back from the anchor."""
    rows = []
    for week, n in enumerate(counts_by_week):
        rows += [(week * 7 + (i % 7), f"{text} number {i} week {week}", "money_movement") for i in range(n)]
    # Filler so every theme has data and the complaint's terms are not "too common".
    rows += [(i % 80, f"bank balance wallet topic {i}", "money_movement") for i in range(60)]
    rows += [(i % 80, f"chart candle intraday option {i}", "trading_charts") for i in range(60)]
    rows += [(i % 80, f"brokerage charges high {i}", "fees_brokerage") for i in range(40)]
    rows += [(i % 80, f"app crash hang slow {i}", "reliability") for i in range(40)]
    rows += [(i % 80, f"nice thing number {i}", "other") for i in range(40)]
    return rows


def settings(**overrides) -> PatternSettings:
    return load_settings().pattern.model_copy(update=overrides)


COMPLAINT = "my withdrawal is stuck and money not received"


# ---------------------------------------------------------------------------
# Theme classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, theme_id",
    [
        ("My withdrawal has been stuck for three days", "money_movement"),
        ("Money debited but not credited, UPI payment failed", "money_movement"),
        ("kyc pending, paisa nahi nikal raha", "money_movement"),
        ("The refund for my deposit never came", "money_movement"),
        ("app crashes every time I open it after the update", "reliability"),
        ("app bahut slow hai, hang ho jata hai", "reliability"),
        ("The app freezes and keeps showing an error on login", "reliability"),
        ("charts are lagging and candle data is wrong", "trading_charts"),
        ("options order not executed at limit price", "trading_charts"),
        ("F&O intraday square off failed", "trading_charts"),
        ("brokerage charges are too high, hidden fees", "fees_brokerage"),
        ("bahut zyada charges kat rahe hain", "fees_brokerage"),
        ("why is brokerage so costly, too many charges", "fees_brokerage"),
        ("good app", "other"),
        ("zzzz qqqq plokm", "other"),
    ],
)
def test_sample_complaints_map_to_the_right_theme(agent, text, theme_id):
    assert agent.classify_theme(text) == theme_id
    assert agent.run(text).theme_id == theme_id


def test_ties_go_to_the_theme_listed_first_in_priority(agent):
    # One strong word each for fees ("brokerage") and trading ("chart").
    assert agent.classify_theme("brokerage chart") == "fees_brokerage"


def test_the_classifier_agrees_with_the_dataset_labels_most_of_the_time(agent, data):
    """Agreement with the labelled themes. The labels are coarse (a chart bug is filed under
    trading), so this is a sanity floor and not an accuracy claim."""
    named = [r for r in data.themes.reviews.values() if data.themes.review_theme[r.review_id] != "other"]
    hits = sum(agent.classify_theme(r.text) == data.themes.review_theme[r.review_id] for r in named)
    assert hits / len(named) >= 0.70


def test_theme_classifier_hook_is_used_only_when_rules_miss_and_is_checked(data):
    class Hook:
        def __init__(self, answer):
            self.answer, self.calls = answer, 0

        def classify(self, question):
            self.calls += 1
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer

    hook = Hook("reliability")
    agent = PatternAgent(data.themes, classifier=hook)
    assert agent.classify_theme("zzzz qqqq plokm") == "reliability"
    assert agent.classify_theme("brokerage charges too high") == "fees_brokerage"
    assert hook.calls == 1
    for bad in ("not_a_theme", None, ValueError("boom")):
        assert PatternAgent(data.themes, classifier=Hook(bad)).classify_theme("zzzz qqqq plokm") == "other"


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


def test_windows_end_on_the_newest_review_and_not_on_today(agent, data):
    assert agent.anchor_date == data.themes.anchor_date == ANCHOR
    result = agent.run(COMPLAINT)
    assert result.window.end == ANCHOR
    assert result.window.start == ANCHOR - timedelta(days=6)
    assert result.window.days == 7
    assert result.window.anchor_date == ANCHOR
    assert result.window.end != date.today()


def test_shift_moves_the_window_back_a_week(agent):
    result = agent.run(COMPLAINT, window_shift_weeks=1)
    assert result.window.end == ANCHOR - timedelta(days=7)
    assert result.window.start == ANCHOR - timedelta(days=13)
    assert result.window.anchor_date == ANCHOR


def test_a_window_before_the_data_starts_has_zero_counts(agent):
    result = agent.run(COMPLAINT, window_shift_weeks=40)
    assert result.status == "ok"
    assert (result.count_current, result.count_prior) == (0, 0)
    assert result.signal == "one_off"
    assert result.matched_review_ids == ()


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_weekly_counts_match_an_independent_recount(agent, data, theme_id):
    review_ids = data.themes.themes[theme_id].review_ids
    expected = []
    for week in range(12):
        end = ANCHOR - timedelta(days=7 * week)
        start = end - timedelta(days=6)
        expected.append(sum(start <= data.themes.reviews[r].date.date() <= end for r in review_ids))
    assert agent.weekly_counts(theme_id, 12) == expected
    assert sum(expected) == len(review_ids)  # the 12 windows cover the whole data set


def test_theme_level_counts_equal_the_independent_recount(agent, data):
    result = agent.run("zzzz qqqq plokm", theme_hint="money_movement")
    assert result.basis == "theme_level"
    weekly = agent.weekly_counts("money_movement", 2)
    assert (result.count_current, result.count_prior) == (weekly[0], weekly[1])


# ---------------------------------------------------------------------------
# Keyword narrowing
# ---------------------------------------------------------------------------


def test_keyword_match_counts_only_similar_reviews():
    themes = make_themes(withdrawals([6, 2]) + [(1, "withdrawal of dividend question only", "money_movement")])
    result = PatternAgent(themes, settings=settings()).run(COMPLAINT)
    assert result.basis == "keyword_match"
    assert result.theme_id == "money_movement"
    assert (result.count_current, result.count_prior) == (6, 2)


def test_no_matching_review_falls_back_to_the_theme_level(agent):
    result = agent.run("zzzz qqqq plokm", theme_hint="reliability")
    assert result.status == "ok"
    assert result.basis == "theme_level"
    assert result.theme_id == "reliability"
    assert result.count_current > 0


def test_a_complaint_with_no_keyword_match_still_returns_a_valid_result(agent):
    result = agent.run("zzzz qqqq plokm")
    assert result.status == "ok"
    assert result.basis == "theme_level"
    assert PatternResult.model_validate_json(result.model_dump_json()) == result


def test_other_at_theme_level_never_raises_a_signal(agent):
    result = agent.run("zzzz qqqq plokm")
    assert result.theme_id == "other"
    assert result.signal == "one_off"
    assert result.count_current > 0  # the count is still shown


def test_theme_level_is_never_rising():
    # Whole theme: 12 this week against 2 before. A keyword match would be "rising".
    themes = make_themes(withdrawals([12, 2]))
    result = PatternAgent(themes, settings=settings()).run("zzzz qqqq plokm", theme_hint="money_movement")
    assert result.basis == "theme_level"
    assert result.signal == "recurring"
    assert PatternAgent(themes, settings=settings()).run(COMPLAINT).signal == "rising"


def test_hinglish_terms_are_matched():
    themes = make_themes(
        [(i % 5, f"paisa nahi nikal raha number {i}", "money_movement") for i in range(8)]
        + [(i % 80, f"bank balance wallet topic {i}", "money_movement") for i in range(60)]
        + [(i, f"chart candle {i}", "trading_charts") for i in range(30)]
    )
    result = PatternAgent(themes, settings=settings()).run("mera paisa nikal nahi raha")
    assert result.theme_id == "money_movement"
    assert result.basis == "keyword_match"
    assert result.count_current == 8


def test_very_common_words_are_not_used_as_evidence():
    # "app" is a stop word, and "money" appears in every review of the theme.
    themes = make_themes(
        [(i % 7, f"money app review {i}", "money_movement") for i in range(30)]
        + [(i, f"chart candle {i}", "trading_charts") for i in range(30)]
    )
    result = PatternAgent(themes, settings=settings()).run("money app", theme_hint="money_movement")
    assert result.basis == "theme_level"


# ---------------------------------------------------------------------------
# Signal rules and thresholds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "weeks, signal",
    [
        ([5, 3], "rising"),  # at MIN_COUNT, ratio 1.67
        ([4, 1], "one_off"),  # just below MIN_COUNT, and 1 of 4 weeks
        ([5, 4], "recurring"),  # at MIN_COUNT, ratio 1.25 is under 1.5
        ([6, 4], "rising"),  # ratio exactly 1.5
        ([8, 6], "recurring"),  # ratio 1.33
        ([5, 0], "rising"),  # prior window empty, no division
        ([1, 1, 1, 0], "recurring"),  # present in 3 of the last 4 weeks
        ([1, 0, 1, 1], "recurring"),
        ([2, 0], "one_off"),  # below MIN_COUNT, in 1 of 4 weeks
        ([1, 0, 1, 0], "one_off"),  # present in only 2 of 4
        ([2, 0, 0, 0], "one_off"),
    ],
)
def test_signal_rules_at_the_thresholds(weeks, signal):
    themes = make_themes(withdrawals(weeks))
    result = PatternAgent(themes, settings=settings(min_count=5, rising_ratio=1.5)).run(COMPLAINT)
    assert result.basis == "keyword_match"
    assert result.signal == signal, (weeks, result)


def test_thresholds_come_from_settings():
    themes = make_themes(withdrawals([3, 1]))
    assert PatternAgent(themes, settings=settings(min_count=5)).run(COMPLAINT).signal == "one_off"
    assert PatternAgent(themes, settings=settings(min_count=3)).run(COMPLAINT).signal == "rising"
    themes = make_themes(withdrawals([6, 4]))
    assert PatternAgent(themes, settings=settings(rising_ratio=1.6)).run(COMPLAINT).signal == "recurring"


def test_all_three_signals_are_produced():
    seen = {
        PatternAgent(make_themes(withdrawals(weeks)), settings=settings()).run(COMPLAINT).signal
        for weeks in ([6, 2], [6, 6], [2, 0])
    }
    assert seen == {"rising", "recurring", "one_off"}


def test_all_three_signals_occur_on_real_complaints(agent, data):
    """The tuning check: every signal appears when low-rated reviews are used as complaints."""
    seen = set()
    for review in data.themes.reviews.values():
        if review.rating <= 2:
            seen.add(agent.run(review.text).signal)
    assert seen == {"one_off", "recurring", "rising"}


def test_a_shifted_window_can_find_an_earlier_surge():
    # Surge two weeks ago: 9 reviews, against 2 before it.
    themes = make_themes(withdrawals([1, 1, 9, 2]))
    agent = PatternAgent(themes, settings=settings())
    assert agent.run(COMPLAINT).signal != "rising"
    shifted = agent.run(COMPLAINT, window_shift_weeks=2)
    assert shifted.signal == "rising"
    assert (shifted.count_current, shifted.count_prior) == (9, 2)


# ---------------------------------------------------------------------------
# Output: IDs and counts only
# ---------------------------------------------------------------------------


def test_matched_ids_are_current_window_reviews_of_the_theme(agent, data):
    result = agent.run("zzzz qqqq plokm", theme_hint="money_movement")
    assert 0 < len(result.matched_review_ids) <= 20
    for review_id in result.matched_review_ids:
        assert re.fullmatch(r"[0-9a-f]{16}", review_id)
        assert data.themes.review_theme[review_id] == "money_movement"
        assert result.window.start <= data.themes.reviews[review_id].date.date() <= result.window.end
    newest_first = [data.themes.reviews[r].date.date() for r in result.matched_review_ids]
    assert newest_first == sorted(newest_first, reverse=True)


def test_example_ids_are_capped(agent):
    result = agent.run("zzzz qqqq plokm", theme_hint="trading_charts")
    assert result.count_current > 20
    assert len(result.matched_review_ids) == 20


def test_no_review_text_appears_in_any_result(agent, data):
    texts = [r.text for r in data.themes.reviews.values() if len(r.text) > 25]
    for review in list(data.themes.reviews.values())[:150]:
        dumped = agent.run(review.text).model_dump_json()
        assert review.text[:25] not in dumped if len(review.text) > 25 else True
        assert not any(t[:40] in dumped for t in texts[:200])


def test_the_complaint_never_appears_in_the_result(agent):
    for text in ("zxqv-unique-marker withdrawal", "zxqv-unique-marker nothing known", "zxqv-unique-marker app crash"):
        assert "zxqv" not in agent.run(text).model_dump_json()


def test_results_are_valid_contract_objects(agent):
    for text in (COMPLAINT, "app crashes", "brokerage charges", "zzzz", "good app"):
        result = agent.run(text)
        assert PatternResult.model_validate_json(result.model_dump_json()) == result


def test_same_input_gives_the_same_result(agent):
    assert agent.run(COMPLAINT) == agent.run(COMPLAINT)


# ---------------------------------------------------------------------------
# Hints, errors and failures
# ---------------------------------------------------------------------------


def test_theme_hint_skips_the_classifier(agent):
    result = agent.run("brokerage charges too high", theme_hint="reliability")
    assert result.theme_id == "reliability"


@pytest.mark.parametrize(
    "kwargs, code",
    [
        ({"theme_hint": "weather"}, "PATTERN_BAD_THEME_HINT"),
        ({"window_shift_weeks": -1}, "PATTERN_BAD_WINDOW"),
        ({"window_shift_weeks": 53}, "PATTERN_BAD_WINDOW"),
        ({"window_shift_weeks": True}, "PATTERN_BAD_WINDOW"),
        ({"window_shift_weeks": 1.5}, "PATTERN_BAD_WINDOW"),
    ],
)
def test_bad_parameters_are_error_results(agent, kwargs, code):
    result = agent.run(COMPLAINT, **kwargs)
    assert result.status == "error"
    assert result.error == code


@pytest.mark.parametrize("raw", ["", "   ", None, 7])
def test_unusable_input_is_an_error_result_not_an_exception(agent, raw):
    result = agent.run(raw)  # type: ignore[arg-type]
    assert result.status == "error"
    assert result.error == "PATTERN_BAD_INPUT"


def test_an_internal_failure_becomes_an_error_without_the_text(data, monkeypatch):
    agent = PatternAgent(data.themes)

    def boom(*args, **kwargs):
        raise RuntimeError("could not parse: SECRET-COMPLAINT-TEXT")

    monkeypatch.setattr(agent, "_key_terms", boom)
    result = agent.run("SECRET-COMPLAINT-TEXT withdrawal")
    assert result.status == "error"
    assert result.error == "PATTERN_INTERNAL_ERROR"
    assert "SECRET" not in result.model_dump_json()


def test_running_the_agent_does_not_change_the_data(data):
    agent = PatternAgent(data.themes)
    before = (len(data.themes.reviews), dict(data.themes.review_theme), data.themes.anchor_date)
    agent.run(COMPLAINT)
    assert (len(data.themes.reviews), dict(data.themes.review_theme), data.themes.anchor_date) == before


# ---------------------------------------------------------------------------
# Real data
# ---------------------------------------------------------------------------


def test_every_real_review_gives_a_valid_result(agent, data):
    results = [agent.run(r.text) for r in data.themes.reviews.values()]
    assert len(results) == 1650
    assert all(r.status == "ok" for r in results)
    assert all(r.basis in {"keyword_match", "theme_level"} for r in results)
    assert {r.basis for r in results} == {"keyword_match", "theme_level"}


# ---------------------------------------------------------------------------
# Text helpers and vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "words",
    [
        ("withdrawal", "withdrawals", "withdraw"),
        ("charge", "charges", "charging"),
        ("crash", "crashes", "crashing"),
        ("trade", "trading", "trader"),
        ("update", "updated", "updates"),
    ],
)
def test_stemmer_joins_word_forms(words):
    assert len({stem(w) for w in words}) == 1


def test_tokens_keep_hinglish_and_drop_digits():
    assert tokens("Paisa nahi aaya 500 rupees!") == ["paisa", "nahi", "aaya", "rupees"]


def test_vocabulary_lists_every_scored_theme():
    vocab = load_vocabulary()
    assert set(vocab.priority) == set(THEME_IDS) - {"other"}
    assert all(vocab.strong[t] for t in vocab.priority)


def test_vocabulary_with_a_missing_theme_is_rejected(tmp_path):
    text = (SRC.parent / "config" / "pattern.toml").read_text(encoding="utf-8")
    path = tmp_path / "pattern.toml"
    path.write_text(text.replace('priority = ["fees_brokerage", "money_movement", "reliability", "trading_charts"]',
                                 'priority = ["fees_brokerage", "money_movement", "reliability"]'), encoding="utf-8")
    with pytest.raises(ValueError):
        load_vocabulary(path)


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_pattern_does_not_import_the_other_agents():
    tree = ast.parse((SRC / "agents" / "pattern.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = {"agents.facts", "agents.compliance", "agents.stubs", "ingress", "ingress.redactor"}
    assert not (imported & forbidden), imported & forbidden


def test_pattern_never_writes_logs_or_touches_the_network():
    source = (SRC / "agents" / "pattern.py").read_text(encoding="utf-8")
    for banned in ("write_text", "write_bytes", "requests", "urllib", "socket", "print(", "logging"):
        assert banned not in source, banned
    assert source.count("open(") == 1 and 'open(path or PATTERN_CONFIG_PATH, "rb")' in source


def test_the_result_has_only_contract_fields(agent):
    assert set(json.loads(agent.run(COMPLAINT).model_dump_json())) == set(PatternResult.model_fields)
