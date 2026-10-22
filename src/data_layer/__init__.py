"""Read-only data layer: scheme corpus and review themes.

Typical use::

    from data_layer import load_all
    data = load_all()
    data.corpus.get("mid_cap").sections["Exit load"]
    data.themes.anchor_date
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from data_layer import corpus, themes
from data_layer.corpus import Corpus
from data_layer.errors import DataValidationError
from data_layer.themes import Themes


@dataclass(frozen=True)
class DataLayer:
    corpus: Corpus
    themes: Themes


def load_all(data_dir: Path | None = None) -> DataLayer:
    """Load both sources. Any mismatch raises ``DataValidationError`` at startup."""
    return DataLayer(corpus=corpus.load(data_dir), themes=themes.load(data_dir))


__all__ = ["DataLayer", "DataValidationError", "Corpus", "Themes", "load_all", "corpus", "themes"]
