"""Milestone 1 corpus loader: five HDFC scheme pages in ``data/parsed/``.

``load()`` reads ``parse_manifest.json`` and the five scheme files. The manifest
is used to find the files and to check that all five schemes are present. The facts
themselves come from the per-scheme files.

Everything returned is read-only: frozen dataclasses, tuples and mapping proxies.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from constants import ALLOWED_CITATIONS, SCHEME_IDS, SCHEME_SOURCE_URLS, SECTION_TITLES
from data_layer.errors import DataValidationError
from paths import DATA_DIR


@dataclass(frozen=True)
class TextBlock:
    section_title: str
    text: str


@dataclass(frozen=True)
class Scheme:
    scheme_id: str
    scheme_name: str
    source_url: str
    nav_date: str
    fetched_at: str
    text_blocks: tuple[TextBlock, ...]

    @property
    def sections(self) -> Mapping[str, str]:
        """``section_title -> text``, read-only."""
        return MappingProxyType({block.section_title: block.text for block in self.text_blocks})


@dataclass(frozen=True)
class Corpus:
    schemes: Mapping[str, Scheme]
    parsed_at: str

    def get(self, scheme_id: str) -> Scheme:
        return self.schemes[scheme_id]

    @property
    def source_urls(self) -> frozenset[str]:
        return frozenset(scheme.source_url for scheme in self.schemes.values())


def _read_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise DataValidationError(f"missing data file: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise DataValidationError(f"{path.name} is not valid JSON") from exc
    if not isinstance(data, dict):
        raise DataValidationError(f"{path.name} must contain a JSON object")
    return data


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DataValidationError(message)


def _load_scheme(parsed_dir: Path, entry: Mapping) -> Scheme:
    scheme_id = entry.get("scheme_id")
    _require(scheme_id in SCHEME_IDS, f"manifest lists an unknown scheme: {scheme_id!r}")

    # Use the file name only, so a manifest entry cannot point outside the folder.
    file_name = Path(str(entry.get("file", ""))).name
    raw = _read_json(parsed_dir / file_name)

    for key in ("scheme_id", "scheme_name", "nav_date", "fetched_at"):
        _require(
            raw.get(key) == entry.get(key),
            f"{file_name}: '{key}' does not match parse_manifest.json",
        )

    source_url = raw.get("source_url")
    _require(
        source_url == SCHEME_SOURCE_URLS[scheme_id],
        f"{file_name}: source_url is not the expected citation link for {scheme_id}",
    )
    _require(source_url in ALLOWED_CITATIONS, f"{file_name}: source_url is not allowed")

    blocks_raw = raw.get("text_blocks")
    _require(isinstance(blocks_raw, list), f"{file_name}: text_blocks must be a list")
    blocks: list[TextBlock] = []
    for item in blocks_raw:
        _require(
            isinstance(item, dict)
            and isinstance(item.get("section_title"), str)
            and isinstance(item.get("text"), str)
            and item["text"].strip() != "",
            f"{file_name}: every text block needs a section_title and non-empty text",
        )
        blocks.append(TextBlock(section_title=item["section_title"], text=item["text"]))

    titles = [block.section_title for block in blocks]
    _require(
        len(titles) == len(set(titles)) and set(titles) == set(SECTION_TITLES),
        f"{file_name}: expected exactly the seven sections {list(SECTION_TITLES)}, got {titles}",
    )
    _require(
        raw.get("text_block_count") == len(blocks) == entry.get("text_block_count"),
        f"{file_name}: text_block_count does not match the blocks present",
    )

    return Scheme(
        scheme_id=scheme_id,
        scheme_name=raw["scheme_name"],
        source_url=source_url,
        nav_date=raw["nav_date"],
        fetched_at=raw["fetched_at"],
        text_blocks=tuple(blocks),
    )


def load(data_dir: Path | None = None) -> Corpus:
    """Load and validate the scheme corpus. Raises ``DataValidationError`` on any mismatch."""
    parsed_dir = (data_dir or DATA_DIR) / "parsed"
    manifest = _read_json(parsed_dir / "parse_manifest.json")

    entries = manifest.get("schemes")
    _require(isinstance(entries, list), "parse_manifest.json: 'schemes' must be a list")
    _require(
        manifest.get("scheme_count") == len(entries) == len(SCHEME_IDS),
        f"parse_manifest.json: expected {len(SCHEME_IDS)} schemes",
    )
    manifest_ids = [entry.get("scheme_id") for entry in entries]
    _require(
        len(set(manifest_ids)) == len(manifest_ids) and set(manifest_ids) == set(SCHEME_IDS),
        f"parse_manifest.json: expected schemes {list(SCHEME_IDS)}, got {manifest_ids}",
    )

    schemes = {entry["scheme_id"]: _load_scheme(parsed_dir, entry) for entry in entries}
    return Corpus(
        schemes=MappingProxyType(schemes),
        parsed_at=str(manifest.get("parsed_at", "")),
    )
