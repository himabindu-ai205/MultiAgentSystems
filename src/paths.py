"""Project locations."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.toml"
COMPLIANCE_CONFIG_PATH = PROJECT_ROOT / "config" / "compliance.toml"
PATTERN_CONFIG_PATH = PROJECT_ROOT / "config" / "pattern.toml"
FACTS_CONFIG_PATH = PROJECT_ROOT / "config" / "facts.toml"
