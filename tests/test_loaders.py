"""Phase 0 exit criteria: loading, validation and read-only behaviour."""

import ast
import hashlib
import json
import shutil
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path

import pytest

from constants import ALLOWED_CITATIONS, SCHEME_IDS, SECTION_TITLES, THEME_IDS
from data_layer import DataValidationError, load_all
from paths import DATA_DIR, PROJECT_ROOT

# ---------------------------------------------------------------------------
# Loading the real data
# ---------------------------------------------------------------------------


def test_corpus_has_five_schemes_with_seven_sections_each(data):
    corpus = data.corpus
    assert set(corpus.schemes) == set(SCHEME_IDS)
    assert len(corpus.schemes) == 5
    total_sections = 0
    for scheme in corpus.schemes.values():
        assert len(scheme.text_blocks) == 7
        assert set(scheme.sections) == set(SECTION_TITLES)
        total_sections += len(scheme.sections)
    assert total_sections == 35


def test_each_scheme_exposes_the_documented_fields(data):
    scheme = data.corpus.get("mid_cap")
    assert scheme.scheme_id == "mid_cap"
    assert scheme.scheme_name == "HDFC Mid Cap Fund Direct Growth"
    assert scheme.source_url == "https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth"
    assert scheme.nav_date == "25-Jun-2026"
    assert scheme.fetched_at.startswith("2026-06-28")
    assert scheme.sections["Exit load"] == "Exit load of 1% if redeemed within 1 year."


def test_corpus_source_urls_are_the_allowed_citations(data):
    """The constants and the real corpus must agree, both ways."""
    assert data.corpus.source_urls == ALLOWED_CITATIONS


def test_themes_and_review_index_counts(data):
    themes = data.themes
    assert themes.review_count == 1650
    assert len(themes.review_dates) == 1650
    assert set(themes.themes) == set(THEME_IDS)
    assert len(themes.themes) == 5
    assert sum(theme.count for theme in themes.themes.values()) == 1650
    assert set(themes.top_theme_ids) == {"trading_charts", "money_movement", "reliability"}


def test_every_review_is_assigned_to_exactly_one_theme(data):
    themes = data.themes
    assert set(themes.review_theme) == set(themes.reviews)
    seen = [rid for theme in themes.themes.values() for rid in theme.review_ids]
    assert len(seen) == len(set(seen)) == 1650


def test_anchor_date_is_the_newest_review_date(data):
    assert data.themes.anchor_date == date(2026, 7, 4)
    oldest = min(data.themes.review_dates.values())
    assert oldest.date() == date(2026, 4, 12)


def test_review_dates_are_timezone_aware(data):
    assert all(d.tzinfo is not None for d in data.themes.review_dates.values())


# ---------------------------------------------------------------------------
# Read-only
# ---------------------------------------------------------------------------


def test_loaded_structures_cannot_be_changed(data):
    corpus, themes = data.corpus, data.themes
    scheme = corpus.get("large_cap")

    with pytest.raises(TypeError):
        corpus.schemes["new"] = scheme  # type: ignore[index]
    with pytest.raises(TypeError):
        scheme.sections["Exit load"] = "changed"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        scheme.source_url = "https://example.com"  # type: ignore[misc]
    with pytest.raises(TypeError):
        themes.themes["other"] = None  # type: ignore[index]
    with pytest.raises(TypeError):
        themes.reviews["x"] = None  # type: ignore[index]
    with pytest.raises(TypeError):
        themes.review_dates["x"] = None  # type: ignore[index]
    with pytest.raises(AttributeError):
        scheme.text_blocks.append(None)  # type: ignore[attr-defined]


def _digest_tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_loading_does_not_write_to_data():
    before = _digest_tree(DATA_DIR)
    load_all()
    load_all()
    assert _digest_tree(DATA_DIR) == before


def test_no_source_file_opens_a_file_for_writing():
    """Static check: nothing under src/ calls open() with a write, append or create mode."""
    offenders = []
    for path in (PROJECT_ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open"):
                continue
            mode_nodes = list(node.args[1:2]) + [kw.value for kw in node.keywords if kw.arg == "mode"]
            for mode in mode_nodes:
                if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
                    if any(flag in mode.value for flag in "wax+"):
                        offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_data_layer_never_calls_write_helpers():
    # Methods that only ever write or delete files, whatever they are called on.
    banned_methods = {"write_text", "write_bytes", "unlink", "rmdir", "touch"}
    # Functions in modules that change the file system.
    banned_modules = {"os", "shutil"}
    offenders = []
    for path in (PROJECT_ROOT / "src" / "data_layer").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in banned_methods:
                offenders.append(f"{path.name}:{node.lineno}:{node.attr}")
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in banned_modules
            ):
                offenders.append(f"{path.name}:{node.lineno}:{node.value.id}.{node.attr}")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                if any(n.split(".")[0] in banned_modules for n in names):
                    offenders.append(f"{path.name}:{node.lineno}:import")
    assert offenders == []


# ---------------------------------------------------------------------------
# Startup validation catches bad data
# ---------------------------------------------------------------------------


@pytest.fixture()
def data_copy(tmp_path):
    target = tmp_path / "data"
    shutil.copytree(DATA_DIR, target)
    return target


def _edit_json(path: Path, edit):
    content = json.loads(path.read_text(encoding="utf-8"))
    edit(content)
    path.write_text(json.dumps(content), encoding="utf-8")


def test_copy_of_real_data_loads(data_copy):
    assert len(load_all(data_copy).corpus.schemes) == 5


def test_missing_section_is_rejected(data_copy):
    def drop_section(content):
        content["text_blocks"] = [
            b for b in content["text_blocks"] if b["section_title"] != "Exit load"
        ]
        content["text_block_count"] = len(content["text_blocks"])

    _edit_json(data_copy / "parsed" / "large_cap.json", drop_section)
    with pytest.raises(DataValidationError):
        load_all(data_copy)


def test_unexpected_citation_link_is_rejected(data_copy):
    _edit_json(
        data_copy / "parsed" / "mid_cap.json",
        lambda c: c.update(source_url="https://example.com/not-the-corpus"),
    )
    with pytest.raises(DataValidationError):
        load_all(data_copy)


def test_missing_scheme_in_manifest_is_rejected(data_copy):
    def drop_scheme(content):
        content["schemes"] = content["schemes"][:-1]
        content["scheme_count"] = len(content["schemes"])

    _edit_json(data_copy / "parsed" / "parse_manifest.json", drop_scheme)
    with pytest.raises(DataValidationError):
        load_all(data_copy)


def test_manifest_and_scheme_file_disagreeing_is_rejected(data_copy):
    _edit_json(
        data_copy / "parsed" / "small_cap.json", lambda c: c.update(nav_date="01-Jan-2020")
    )
    with pytest.raises(DataValidationError):
        load_all(data_copy)


def test_review_in_two_themes_is_rejected(data_copy):
    def duplicate(content):
        themes = {t["id"]: t for t in content["allThemes"]}
        stolen = themes["other"]["reviewIds"][0]
        themes["fees_brokerage"]["reviewIds"].append(stolen)
        themes["fees_brokerage"]["count"] += 1

    _edit_json(data_copy / "analyzed_themes.json", duplicate)
    with pytest.raises(DataValidationError):
        load_all(data_copy)


def test_unassigned_review_is_rejected(data_copy):
    def unassign(content):
        theme = next(t for t in content["allThemes"] if t["id"] == "other")
        theme["reviewIds"].pop()
        theme["count"] -= 1

    _edit_json(data_copy / "analyzed_themes.json", unassign)
    with pytest.raises(DataValidationError):
        load_all(data_copy)


def test_review_count_mismatch_is_rejected(data_copy):
    _edit_json(data_copy / "analyzed_themes.json", lambda c: c.update(reviewCount=1))
    with pytest.raises(DataValidationError):
        load_all(data_copy)


def test_missing_file_is_rejected(data_copy):
    (data_copy / "normalized_reviews.json").unlink()
    with pytest.raises(DataValidationError):
        load_all(data_copy)
