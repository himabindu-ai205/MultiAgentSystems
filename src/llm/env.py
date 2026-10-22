"""A small loader for the ``.env`` file that holds API keys.

It reads ``NAME=value`` lines and never overrides a variable that is already set, so a key
exported in the shell wins over the file. It reports the *names* it loaded and never the
values, so the result is safe to log or print.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, MutableMapping
from pathlib import Path

from paths import PROJECT_ROOT

ENV_PATH = PROJECT_ROOT / ".env"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def load_env_file(
    path: Path | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> list[str]:
    """Copy variables from the file into ``environ`` (``os.environ`` by default).

    Blank values, comments and lines that are not ``NAME=value`` are skipped. Returns the
    names that were set. A missing file is not an error.
    """
    target = os.environ if environ is None else environ
    source = path or ENV_PATH
    try:
        lines = source.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []

    loaded: list[str] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        name, separator, value = line.partition("=")
        name, value = name.strip(), _unquote(value.strip())
        if not separator or not _NAME.match(name) or not value:
            continue
        if target.get(name, "").strip():
            continue  # already set: the environment wins
        target[name] = value
        loaded.append(name)
    return loaded


KEY_NAMES = ("GOOGLE_API_KEY", "GEMINI_API_KEY")
GROQ_KEY_NAMES = ("GROQ_API_KEY",)


def environment_with_env_file() -> dict[str, str]:
    """A copy of the process environment plus ``.env``. The process environment is unchanged."""
    merged = dict(os.environ)
    load_env_file(environ=merged)
    return merged


def require_google_key(environ: Mapping[str, str], *, setting: str) -> str:
    """The Gemini key, or a clear error that names the setting that needs it."""
    for name in KEY_NAMES:
        value = environ.get(name, "").strip()
        if value:
            return value
    raise RuntimeError(
        f"{setting} is set to use Gemini, but "
        + " or ".join(KEY_NAMES)
        + " is not set. Add it to .env, or set the provider to \"none\" to run on rules alone."
    )


def require_groq_key(environ: Mapping[str, str], *, setting: str) -> str:
    """The Groq key, or a clear error that names the setting that needs it."""
    for name in GROQ_KEY_NAMES:
        value = environ.get(name, "").strip()
        if value:
            return value
    raise RuntimeError(
        f"{setting} is set to use Groq, but GROQ_API_KEY is not set. "
        'Add it to .env, or set the provider to "none" to run on rules alone.'
    )


__all__ = [
    "ENV_PATH",
    "GROQ_KEY_NAMES",
    "KEY_NAMES",
    "environment_with_env_file",
    "load_env_file",
    "require_google_key",
    "require_groq_key",
]
