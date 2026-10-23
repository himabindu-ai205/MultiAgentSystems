"""Settings loaded from ``config/settings.toml``.

Decisions that are still open are empty strings in the file. ``Settings.unresolved``
lists them, and code that needs one must call ``require`` so it fails with a clear
message instead of guessing.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from constants import MAX_REFINEMENT_ROUNDS, MAX_REPLY_SENTENCES, MAX_REVIEWER_REWORK_ROUNDS
from paths import CONFIG_PATH, DATA_DIR, PROJECT_ROOT


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


LlmUse = Literal["facts", "pattern", "compliance", "drafter", "reviewer", "refinement"]
LLM_USES: tuple[str, ...] = ("facts", "pattern", "compliance", "drafter", "reviewer", "refinement")


class ModelSettings(_Section):
    provider: str = ""
    model: str = ""


class LlmSettings(_Section):
    """One model entry per use. ``provider`` and ``model`` are the default for any use
    that leaves its own entry empty."""

    provider: str = ""
    model: str = ""
    facts: ModelSettings = ModelSettings()
    pattern: ModelSettings = ModelSettings()
    compliance: ModelSettings = ModelSettings()
    drafter: ModelSettings = ModelSettings()
    # The Reviewer's outbound Compliance call. Kept separate from ``compliance``, so the
    # gate on outgoing drafts can use a different model from the inbound check.
    reviewer: ModelSettings = ModelSettings()
    # Labels free-wording feedback in a refinement (Phase 8). It picks the agents to re-run,
    # never the parameters.
    refinement: ModelSettings = ModelSettings()

    def for_use(self, use: LlmUse) -> ModelSettings:
        """The provider and model a use will call, after applying the default."""
        own: ModelSettings = getattr(self, use)
        return ModelSettings(provider=own.provider or self.provider, model=own.model or self.model)


class UiSettings(_Section):
    form: Literal["cli", "web"] = "cli"


class McpSettings(_Section):
    google_doc_id: str = ""
    email_recipient: str = ""
    # Folder that contains dist/index.js for the Gmail MCP server. Empty means
    # the sibling ``MCP Server`` directory, or MCP_SERVER_DIR in the environment.
    server_dir: str = ""
    # HTTPS origin of a hosted Gmail MCP server, e.g. https://gmail-mcp.onrender.com
    # Empty uses MCP_SERVER_URL in the environment. Local stdio is used when both are empty.
    server_url: str = ""


class PatternSettings(_Section):
    min_count: int = Field(5, ge=1)
    rising_ratio: float = Field(1.5, gt=1.0)
    window_days: int = Field(7, ge=1)
    baseline_weeks: int = Field(4, ge=1)


class LimitSettings(_Section):
    reviewer_rework_rounds: int = MAX_REVIEWER_REWORK_ROUNDS
    refinement_rounds: int = MAX_REFINEMENT_ROUNDS
    max_reply_sentences: int = MAX_REPLY_SENTENCES
    agent_timeout_seconds: float = Field(30.0, gt=0)
    max_complaint_chars: int = Field(5000, ge=1)


class Settings(_Section):
    data_dir: str = "data"
    llm: LlmSettings = LlmSettings()
    ui: UiSettings = UiSettings()
    mcp: McpSettings = McpSettings()
    pattern: PatternSettings = PatternSettings()
    limits: LimitSettings = LimitSettings()

    @property
    def data_path(self) -> Path:
        path = Path(self.data_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path

    def unresolved(self) -> list[str]:
        """Dotted names of the decisions that are still empty."""
        open_items: dict[str, str] = {}
        for use in LLM_USES:
            effective = self.llm.for_use(use)  # type: ignore[arg-type]
            open_items[f"llm.{use}.provider"] = effective.provider
            open_items[f"llm.{use}.model"] = effective.model
        open_items["mcp.google_doc_id"] = self.mcp.google_doc_id
        open_items["mcp.email_recipient"] = self.mcp.email_recipient
        return [name for name, value in open_items.items() if not value.strip()]

    def require(self, *names: str) -> None:
        """Raise if any of the named decisions is still open."""
        missing = [name for name in names if name in self.unresolved()]
        if missing:
            raise RuntimeError(
                "Setting(s) not decided yet in config/settings.toml: " + ", ".join(missing)
            )


def load_settings(path: Path | None = None) -> Settings:
    path = path or CONFIG_PATH
    with open(path, "rb") as handle:
        raw = tomllib.load(handle)
    return Settings.model_validate(raw)


# Re-exported so callers have one place to import locations from.
__all__ = ["LLM_USES", "ModelSettings", "Settings", "load_settings", "DATA_DIR"]
