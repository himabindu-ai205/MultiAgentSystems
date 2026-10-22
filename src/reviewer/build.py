"""Build the Reviewer from the settings (Phase 7).

The Reviewer's model, Gemini, backs its outbound Compliance call. If the settings ask for
Gemini and no key is available, this raises instead of quietly building a gate that has
only the rule layer. To run on the rules alone, set the provider to ``none`` on purpose.
"""

from __future__ import annotations

from collections.abc import Mapping

from agents.compliance import ComplianceAgent
from config import Settings
from data_layer.corpus import Corpus
from llm.env import KEY_NAMES, environment_with_env_file, require_google_key
from llm.gemini import GeminiAdviceClassifier, Transport
from logging_allowlist import AllowListLogger
from reviewer.gate import Reviewer


def build_reviewer(
    settings: Settings,
    *,
    corpus: Corpus | None = None,
    logger: AllowListLogger | None = None,
    environ: Mapping[str, str] | None = None,
    transport: Transport | None = None,
) -> Reviewer:
    """The Reviewer, with the model chosen under ``[llm.reviewer]``.

    ``environ`` defaults to the process environment plus ``.env`` (without changing the
    process environment). ``transport`` is for tests.
    """
    settings.require("llm.reviewer.provider")
    chosen = settings.llm.for_use("reviewer")
    provider = chosen.provider.strip().lower()

    classifier: GeminiAdviceClassifier | None = None
    if provider == "gemini":
        settings.require("llm.reviewer.model")
        if environ is None:
            environ = environment_with_env_file()
        classifier = GeminiAdviceClassifier(
            require_google_key(environ, setting="llm.reviewer.provider"),
            chosen.model,
            timeout_seconds=settings.limits.agent_timeout_seconds,
            transport=transport,
        )
    elif provider != "none":
        raise ValueError(f"unsupported reviewer provider: {chosen.provider!r}")

    outbound = ComplianceAgent(corpus=corpus, classifier=classifier).outbound
    return Reviewer(
        outbound_check=outbound,
        logger=logger,
        max_sentences=settings.limits.max_reply_sentences,
    )


__all__ = ["KEY_NAMES", "build_reviewer"]
