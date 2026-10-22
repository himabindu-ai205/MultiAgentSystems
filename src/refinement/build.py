"""Build the refinement router from the settings (Phase 8).

``[llm.refinement]`` chooses the model that labels free-wording feedback. With Gemini and no
key this raises instead of quietly using the word rules alone. Set the provider to ``none``
to use only the rules, on purpose.
"""

from __future__ import annotations

from collections.abc import Mapping

from agents.facts import Vocabulary
from config import Settings
from llm.env import environment_with_env_file, require_google_key
from llm.gemini import GeminiFeedbackClassifier, Transport
from refinement.router import RefinementRouter


def build_router(
    settings: Settings,
    *,
    vocabulary: Vocabulary | None = None,
    environ: Mapping[str, str] | None = None,
    transport: Transport | None = None,
) -> RefinementRouter:
    """The router, with the classifier chosen under ``[llm.refinement]``."""
    settings.require("llm.refinement.provider")
    chosen = settings.llm.for_use("refinement")
    provider = chosen.provider.strip().lower()

    classifier: GeminiFeedbackClassifier | None = None
    if provider == "gemini":
        settings.require("llm.refinement.model")
        if environ is None:
            environ = environment_with_env_file()
        classifier = GeminiFeedbackClassifier(
            require_google_key(environ, setting="llm.refinement.provider"),
            chosen.model,
            timeout_seconds=settings.limits.agent_timeout_seconds,
            transport=transport,
        )
    elif provider != "none":
        raise ValueError(f"unsupported refinement provider: {chosen.provider!r}")

    return RefinementRouter(classifier=classifier, vocabulary=vocabulary)


__all__ = ["build_router"]
