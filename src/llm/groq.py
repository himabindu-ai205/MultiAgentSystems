"""Groq as a label-only classifier and a short drafter (8k tokens/minute).

The free-tier cap is about 8k tokens per minute, and a first run can call Groq for
Facts, Pattern, Compliance and the drafter. Each call therefore:

* sends a one-line system prompt (the allowed labels, nothing else)
* truncates the user text, so a long complaint cannot blow the budget
* caps ``max_completion_tokens`` at a short label or a three-sentence reply
* on gpt-oss, sets ``reasoning_effort`` to ``low`` and drops the chain of thought
  (``include_reasoning=false``), because hidden reasoning still counts against TPM

The specialists only classify. The drafter only rewords a template. Failures become
``LlmError`` with a fixed code; nothing here logs, and the key is a header, never a URL.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from config import Settings
from constants import SECTION_TITLES, THEME_IDS
from llm.env import require_groq_key
from orchestrator.drafter import DraftRequest, build_prompt

BASE_URL = "https://api.groq.com/openai/v1/chat/completions"

# Hard caps. Chosen so four calls in one ticket stay well under 8k TPM.
CLASSIFIER_MAX_COMPLETION_TOKENS = 32
DRAFTER_MAX_COMPLETION_TOKENS = 96
CLASSIFIER_MAX_INPUT_CHARS = 400

_ADVICE = {
    "inbound": ("advice_request", "none"),
    "outbound": ("advice_in_text", "performance_claim", "none"),
}

_SYSTEM = {
    "facts": "One label: " + "|".join((*SECTION_TITLES, "none")),
    "pattern": "One label: " + "|".join((*THEME_IDS, "none")),
    "inbound": "One label: " + "|".join(_ADVICE["inbound"]),
    "outbound": "One label: " + "|".join(_ADVICE["outbound"]),
    "drafter": "Reword the reply. No new facts. Output the reply only.",
}

_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

Transport = Callable[[str, Mapping[str, str], bytes, float], tuple[int, bytes]]


class LlmError(Exception):
    """A model call failed or gave an unusable answer. The message is a fixed code."""


def _urllib_transport(url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""
    except (urllib.error.URLError, TimeoutError, OSError):
        raise LlmError("GROQ_UNREACHABLE") from None


def _clip(text: str, limit: int = CLASSIFIER_MAX_INPUT_CHARS) -> str:
    raw = text if isinstance(text, str) else ""
    raw = raw.strip()
    if len(raw) <= limit:
        return raw
    cut = raw[:limit]
    return cut.rsplit(" ", 1)[0] or cut


def _canonical(said: str, labels: tuple[str, ...]) -> str | None:
    cleaned = said.strip().strip("`'\"").splitlines()[0].strip().rstrip(".").strip() if said else ""
    lowered = cleaned.lower()
    for label in labels:
        if lowered == label.lower():
            return label
    return None


def _content_from(data: Any) -> str:
    if not isinstance(data, dict):
        raise LlmError("GROQ_BAD_RESPONSE")
    choices = data.get("choices")
    if not choices or not isinstance(choices, list) or not isinstance(choices[0], dict):
        raise LlmError("GROQ_BAD_RESPONSE")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise LlmError("GROQ_BAD_RESPONSE")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LlmError("GROQ_BAD_RESPONSE")
    return content


class _GroqCall:
    """One Groq chat completion. Stateless besides the key and the model name."""

    def __init__(
        self,
        api_key: str | None,
        model: str,
        *,
        timeout_seconds: float = 30.0,
        transport: Transport | None = None,
        base_url: str = BASE_URL,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("an API key is required")
        if not isinstance(model, str) or not _MODEL_NAME.match(model.strip()):
            raise ValueError("model must be a Groq model name such as openai/gpt-oss-120b")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._key = api_key.strip()
        self._model = model.strip()
        self._timeout = timeout_seconds
        self._transport = transport or _urllib_transport
        self._url = base_url.rstrip("/")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(model={self._model!r})"

    def _complete(
        self,
        system: str,
        user: str,
        *,
        max_completion_tokens: int,
        stop: list[str] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_completion_tokens": max_completion_tokens,
        }
        if stop:
            payload["stop"] = stop
        if "gpt-oss" in self._model:
            payload["reasoning_effort"] = "low"
            payload["include_reasoning"] = False

        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self._key}"}
        try:
            status, raw = self._transport(self._url, headers, body, self._timeout)
        except LlmError:
            raise
        except Exception:
            raise LlmError("GROQ_UNREACHABLE") from None
        if status != 200:
            raise LlmError(f"GROQ_HTTP_{status}")
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            raise LlmError("GROQ_BAD_RESPONSE") from None
        return _content_from(data)

    def _label(self, text: str, labels: tuple[str, ...], system: str) -> str:
        matched = _canonical(
            self._complete(
                system,
                _clip(text),
                max_completion_tokens=CLASSIFIER_MAX_COMPLETION_TOKENS,
                stop=["\n"],
            ),
            labels,
        )
        if matched is None:
            raise LlmError("GROQ_BAD_LABEL")
        return matched


class GroqSectionClassifier(_GroqCall):
    """Labels a question with one of the seven section titles, or nothing."""

    def classify(self, question: str) -> str | None:
        try:
            got = self._label(question, (*SECTION_TITLES, "none"), _SYSTEM["facts"])
        except LlmError:
            return None
        return None if got == "none" else got


class GroqThemeClassifier(_GroqCall):
    """Labels a complaint with one of the five theme IDs, or nothing."""

    def classify(self, question: str) -> str | None:
        try:
            got = self._label(question, (*THEME_IDS, "none"), _SYSTEM["pattern"])
        except LlmError:
            return None
        return None if got == "none" else got


class GroqAdviceClassifier(_GroqCall):
    """Labels text ``advice_*``, ``performance_claim`` or ``none``. Fail closed."""

    def classify(self, text: str, *, mode: str) -> str:
        labels = _ADVICE.get(mode)
        if labels is None:
            raise LlmError("GROQ_BAD_MODE")
        return self._label(text, labels, _SYSTEM[mode])


class GroqDraftWriter(_GroqCall):
    """Rewords a template. The drafter still checks every fact and citation."""

    def write(self, request: DraftRequest) -> str:
        return self._complete(
            _SYSTEM["drafter"],
            build_prompt(request),
            max_completion_tokens=DRAFTER_MAX_COMPLETION_TOKENS,
        )


@dataclass(frozen=True)
class GroqUses:
    facts: GroqSectionClassifier | None = None
    pattern: GroqThemeClassifier | None = None
    compliance: GroqAdviceClassifier | None = None
    drafter: GroqDraftWriter | None = None


_CLASS = {
    "facts": GroqSectionClassifier,
    "pattern": GroqThemeClassifier,
    "compliance": GroqAdviceClassifier,
    "drafter": GroqDraftWriter,
}


def build_groq_uses(
    settings: Settings,
    *,
    environ: Mapping[str, str],
    transport: Transport | None = None,
    timeout_seconds: float = 30.0,
) -> GroqUses:
    """The Groq-backed hooks for each use that is set to ``groq``. Others stay ``None``."""
    built: dict[str, _GroqCall | None] = {}
    for use, cls in _CLASS.items():
        chosen = settings.llm.for_use(use)  # type: ignore[arg-type]
        provider = chosen.provider.strip().lower()
        if provider in ("", "none"):
            built[use] = None
            continue
        if provider != "groq":
            raise ValueError(f"unsupported {use} provider: {chosen.provider!r}")
        settings.require(f"llm.{use}.model")
        built[use] = cls(
            require_groq_key(environ, setting=f"llm.{use}.provider"),
            chosen.model,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )
    return GroqUses(**built)  # type: ignore[arg-type]


__all__ = [
    "BASE_URL",
    "CLASSIFIER_MAX_COMPLETION_TOKENS",
    "CLASSIFIER_MAX_INPUT_CHARS",
    "DRAFTER_MAX_COMPLETION_TOKENS",
    "GroqAdviceClassifier",
    "GroqDraftWriter",
    "GroqSectionClassifier",
    "GroqThemeClassifier",
    "GroqUses",
    "LlmError",
    "Transport",
    "build_groq_uses",
]
