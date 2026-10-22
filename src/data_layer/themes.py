"""Milestone 2 loader: review themes and the review index.

``load()`` reads ``analyzed_themes.json`` and ``normalized_reviews.json`` once and
joins them. The Pattern Agent then uses the returned index and never scans the
review file per complaint.

Everything returned is read-only.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from types import MappingProxyType

from constants import THEME_IDS
from data_layer.errors import DataValidationError
from paths import DATA_DIR

_REVIEW_ID = re.compile(r"^[0-9a-f]{16}$")


@dataclass(frozen=True)
class Review:
    review_id: str
    text: str
    rating: int
    date: datetime
    version: str | None


@dataclass(frozen=True)
class Theme:
    theme_id: str
    name: str
    description: str
    review_ids: tuple[str, ...]
    count: int
    avg_rating: float
    score: float


@dataclass(frozen=True)
class Themes:
    themes: Mapping[str, Theme]
    top_theme_ids: tuple[str, ...]
    reviews: Mapping[str, Review]
    review_theme: Mapping[str, str]
    anchor_date: date

    @property
    def review_count(self) -> int:
        return len(self.reviews)

    @property
    def review_dates(self) -> Mapping[str, datetime]:
        """``review_id -> date``, read-only."""
        return MappingProxyType({rid: review.date for rid, review in self.reviews.items()})


def _read_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise DataValidationError(f"missing data file: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise DataValidationError(f"{path.name} is not valid JSON") from exc


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DataValidationError(message)


def _parse_timestamp(value: object, where: str) -> datetime:
    _require(isinstance(value, str), f"{where}: date must be a string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DataValidationError(f"{where}: date is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_reviews(path: Path) -> dict[str, Review]:
    raw = _read_json(path)
    _require(isinstance(raw, list), f"{path.name} must contain a JSON list")

    reviews: dict[str, Review] = {}
    for item in raw:
        _require(isinstance(item, dict), f"{path.name}: every review must be an object")
        review_id = item.get("id")
        _require(
            isinstance(review_id, str) and _REVIEW_ID.match(review_id) is not None,
            f"{path.name}: a review has a missing or malformed id",
        )
        _require(review_id not in reviews, f"{path.name}: duplicate review id")
        rating = item.get("rating")
        _require(
            isinstance(rating, int) and not isinstance(rating, bool) and 1 <= rating <= 5,
            f"{path.name}: a review has a rating outside 1-5",
        )
        text = item.get("text")
        _require(isinstance(text, str), f"{path.name}: a review has no text")
        version = item.get("version")
        _require(
            version is None or isinstance(version, str),
            f"{path.name}: a review has a malformed version",
        )
        reviews[review_id] = Review(
            review_id=review_id,
            text=text,
            rating=rating,
            date=_parse_timestamp(item.get("date"), f"{path.name}"),
            version=version,
        )
    _require(reviews, f"{path.name} has no reviews")
    return reviews


def load(data_dir: Path | None = None) -> Themes:
    """Load and validate themes plus the review index. Raises ``DataValidationError``."""
    base = data_dir or DATA_DIR
    reviews = _load_reviews(base / "normalized_reviews.json")

    raw = _read_json(base / "analyzed_themes.json")
    _require(isinstance(raw, dict), "analyzed_themes.json must contain a JSON object")
    _require(
        raw.get("reviewCount") == len(reviews),
        "analyzed_themes.json: reviewCount does not match the number of reviews",
    )

    all_themes = raw.get("allThemes")
    _require(isinstance(all_themes, list), "analyzed_themes.json: 'allThemes' must be a list")

    themes: dict[str, Theme] = {}
    assignments: Counter[str] = Counter()
    review_theme: dict[str, str] = {}
    for item in all_themes:
        _require(isinstance(item, dict), "analyzed_themes.json: every theme must be an object")
        theme_id = item.get("id")
        _require(theme_id in THEME_IDS, f"analyzed_themes.json: unknown theme id {theme_id!r}")
        _require(theme_id not in themes, f"analyzed_themes.json: duplicate theme {theme_id}")

        ids = item.get("reviewIds")
        _require(isinstance(ids, list), f"theme {theme_id}: reviewIds must be a list")
        _require(len(ids) == len(set(ids)), f"theme {theme_id}: duplicate review ids")
        _require(
            item.get("count") == len(ids),
            f"theme {theme_id}: count does not match the number of review ids",
        )
        unknown = [rid for rid in ids if rid not in reviews]
        _require(not unknown, f"theme {theme_id}: {len(unknown)} review id(s) not in the review file")

        for rid in ids:
            assignments[rid] += 1
            review_theme[rid] = theme_id

        themes[theme_id] = Theme(
            theme_id=theme_id,
            name=str(item.get("name", "")),
            description=str(item.get("description", "")),
            review_ids=tuple(ids),
            count=len(ids),
            avg_rating=float(item.get("avgRating", 0.0)),
            score=float(item.get("score", 0.0)),
        )

    _require(
        set(themes) == set(THEME_IDS),
        f"analyzed_themes.json: expected themes {list(THEME_IDS)}, got {list(themes)}",
    )
    doubled = [rid for rid, n in assignments.items() if n > 1]
    _require(not doubled, f"{len(doubled)} review(s) belong to more than one theme")
    missing = set(reviews) - set(assignments)
    _require(not missing, f"{len(missing)} review(s) belong to no theme")

    top = raw.get("topThemes", [])
    _require(isinstance(top, list), "analyzed_themes.json: 'topThemes' must be a list")
    top_ids = tuple(entry.get("id") for entry in top if isinstance(entry, dict))
    _require(
        all(tid in themes for tid in top_ids),
        "analyzed_themes.json: topThemes lists a theme that is not in allThemes",
    )

    anchor = max(review.date for review in reviews.values()).date()
    return Themes(
        themes=MappingProxyType(themes),
        top_theme_ids=top_ids,
        reviews=MappingProxyType(reviews),
        review_theme=MappingProxyType(review_theme),
        anchor_date=anchor,
    )
